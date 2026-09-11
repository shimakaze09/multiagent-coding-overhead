"""Stage 2A - heterogeneous model routing (PREREGISTRATION section 18).

The experimental variable is which model serves each logical role. Everything
else is Arm A's or Arm B's, unchanged:

    S2_R1  Strong-Investigator Hybrid   Coordinator CHEAP  Investigator STRONG  Implementer CHEAP
    S2_R2  Strong-Implementer Hybrid    Coordinator CHEAP  Investigator CHEAP   Implementer STRONG
    S2_R3  All-Cheap Multi              Coordinator CHEAP  Investigator CHEAP   Implementer CHEAP
    S2_S   Single Cheap                 one CHEAP agent (Arm A's prompt and tools)
    S2_SS  Single Strong (fresh)        one STRONG agent; only where no historical
                                        Arm A run has exact parity (section 18.6)

Multi-agent configurations use Arm B's separated topology (Coordinator ->
Investigator -> Coordinator -> Implementer -> Coordinator; the Coordinator is one
session resumed twice; Investigator and Implementer are separate fresh sessions),
Arm B's prompt functions verbatim, Arm B's handoffs (including the forwarded
Investigator report), and Arm B's per-role tools and narrow_pytest_v1 allowlist.
No C1 shared Worker. No prompt is model-specific; the prompt sources are
hash-checked before every run.

Model routing is per invocation (`--model` is a per-process flag). A resumed
Coordinator invocation keeps the Coordinator's model: no configuration changes a
model on `--resume`. After every invocation, the served model is verified from
that invocation's own telemetry (init, assistant messages, modelUsage). On a
mismatch the chain stops before the next session and the run is invalid.

Reasoning effort is not passed to any role (STAGE2_EFFORT_POLICY).
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

import config
from arms import RunSession, finish_run, multi_nl, single, start_run
from harness import agent, events as ev, model_routing
from tasks.registry import Task


class Stage2PromptDrift(RuntimeError):
    """A frozen prompt source changed: Stage 2 must not run on other prompts."""


def _norm_sha(rel: str) -> str:
    return hashlib.sha256((config.STAGE0_ROOT / rel).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def role_appendix_sha() -> str:
    return hashlib.sha256(
        json.dumps(agent.ROLE_SYSTEM_APPENDIX, sort_keys=True).encode("utf-8")).hexdigest()


def check_prompt_sources() -> dict:
    found = {rel: _norm_sha(rel) for rel in config.STAGE2_PROMPT_SOURCES}
    drift = {rel: sha for rel, sha in found.items() if sha != config.STAGE2_PROMPT_SOURCES[rel]}
    appendix = role_appendix_sha()
    if appendix != config.STAGE2_ROLE_APPENDIX_SHA256:
        drift["harness.agent.ROLE_SYSTEM_APPENDIX"] = appendix
    if drift:
        raise Stage2PromptDrift(
            f"Stage-2 prompt sources differ from the frozen versions: {drift}. "
            "Model-specific prompt changes are a separate future experiment.")
    return {"sources_sha256": found, "role_system_appendix_sha256": appendix}


def analysis_versions() -> dict:
    from analysis import decomposition, handoff, isolation, leakage, metrics, overlap_v2
    from harness import claude_cli, telemetry, tools
    return {
        "parser": telemetry.PARSER_VERSION,
        "cli_wrapper": claude_cli.WRAPPER_VERSION,
        "coverage_formula": tools.COVERAGE_FORMULA_VERSION,
        "bash_classifier": tools.BASH_CLASSIFIER_VERSION,
        "reacquisition_classifier": metrics.REACQUISITION_CLASSIFIER_VERSION,
        "handoff_metrics": handoff.HANDOFF_SCHEMA_VERSION,
        "isolation_check": isolation.ISOLATION_CHECK_VERSION,
        "handoff_overlap_v2": overlap_v2.OVERLAP_V2_VERSION,
        "overhead_decomposition": decomposition.DECOMPOSITION_VERSION,
        "held_out_content_check": leakage.LEAKAGE_CONTENT_VERSION,
        "model_routing_check": model_routing.MODEL_ROUTING_CHECK_VERSION,
        "cost_accounting": model_routing.COST_ACCOUNTING_VERSION,
    }


def _verifier_sha(task: Task) -> Optional[str]:
    p = task.verifier_path
    h = hashlib.sha256()
    files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())
    for f in files:
        h.update(str(f.relative_to(p.parent)).replace("\\", "/").encode("utf-8"))
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def stage2_identity(arm: str, cfg: config.RunConfig, session: RunSession) -> dict:
    """Run-level identity: the configuration identity plus what is specific to
    this task and this analysis (base commit, verifier, classifier versions)."""
    ident = {
        "config_hash": config.topology_config_hash(cfg, config.stage2_topology(arm)),
        "base_config_hash": cfg.config_hash(),
        "topology_version": config.STAGE2_TOPOLOGY_VERSION,
        "task_id": session.task.task_id,
        "task_base_commit": session.workspace.base_commit,
        "held_out_verifier_sha256": _verifier_sha(session.task),
        "classifier_versions": analysis_versions(),
    }
    blob = json.dumps(ident, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ident["identity_hash"] = hashlib.blake2b(blob, digest_size=16).hexdigest()
    return ident


class _Router:
    """Runs role invocations with their assigned models and verifies each one."""

    def __init__(self, session: RunSession, topology: dict):
        self.session = session
        self.topology = topology
        self.records: list[dict] = []
        self.stopped: Optional[dict] = None

    def invoke(self, role: str, *, step: str, index: int, prompt: str,
               prompt_inputs: dict, resume: Optional[str] = None):
        cls = self.topology["role_model_classes"][role]
        mc = config.MODEL_CLASSES[cls]
        r = agent.run_agent(
            self.session.ctx, agent.AgentSpec.for_role(role), prompt=prompt,
            workspace=self.session.workspace, session_index=index,
            prompt_inputs=prompt_inputs, resume_session_id=resume, model=mc["requested"])
        self.session.agent_results.append(r)
        obs = model_routing.stream_observation(r.cli.stdout_path)
        check = model_routing.check_invocation(
            requested=r.invocation.model, expected_requested=mc["requested"],
            expected_resolved=mc["expected_resolved"], init=r.cli.parsed.init,
            assistant_models=obs["assistant_message_models"], result=r.cli.parsed.result)
        rec = {
            "session_key": r.session_key,
            "step": step,
            "logical_role": role,
            "physical_session": role,
            "physical_session_id": r.session_id,
            "is_resume": bool(r.invocation.resume_session_id),
            "resume_target": r.invocation.resume_session_id,
            "model_class": cls,
            "requested_model": r.invocation.model,
            "resolved_model": check["resolved_model"],
            "reasoning_effort": model_routing.reasoning_effort_record(),
            "verification": check,
        }
        self.records.append(rec)
        self.session.ctx.log.append(ev.MODEL_ROUTING, agent_id=role, payload=rec)
        return r, rec

    def must_stop(self, r: agent.AgentResult, rec: dict) -> Optional[str]:
        """Checked BEFORE the next session is spawned."""
        v = rec["verification"]
        if not v["verified"]:
            failed = [k for k, ok in v["checks"].items() if ok is False]
            return (f"resolved model does not match the preregistered assignment "
                    f"({rec['model_class']} = {v['expected_resolved_model']}; resolved "
                    f"{v['resolved_model']}; failed {failed})")
        if not r.ok:
            return "agent session did not complete successfully"
        return agent.quota_stop_reason(r.cli)

    def abort(self, step: str, r: agent.AgentResult, reason: str) -> dict:
        self.stopped = {"step": step, "reason": reason}
        self.session.ctx.log.append(ev.ERROR, agent_id=r.spec.agent_id, payload={
            "step": step, "reason": reason, "exit_code": r.cli.exit_code,
            "termination_reason": r.cli.termination_reason,
            "cli_errors": r.cli.parsed.errors[:5],
            "note": "Stage-2 chain stopped early; the held-out verifier still runs."})
        return self.finish()

    def finish(self) -> dict:
        record = {
            "arm": self.topology["arm"],
            "label": self.topology["label"],
            "topology_version": self.topology["topology_version"],
            "role_model_classes": self.topology["role_model_classes"],
            "role_models_requested": self.topology["role_models_requested"],
            "effort_policy": self.topology["effort_policy"],
            "invocations": self.records,
            "all_verified": bool(self.records) and all(x["verification"]["verified"] for x in self.records),
            "stopped": self.stopped,
        }
        (self.session.run_dir / "routing.json").write_text(
            json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        return finish_run(self.session)


def _run_single(rt: _Router, task: Task) -> dict:
    # Arm A's prompt and prompt inputs, exactly.
    rt.invoke("solo", step="solo", index=1, prompt=single.build_solo_prompt(task),
              prompt_inputs={"task_statement_verbatim": task.statement, "upstream_reports": []})
    return rt.finish()


def _run_multi(rt: _Router, task: Task) -> dict:
    """Arm B's orchestration (arms/multi_nl.py), step for step."""
    s = rt.session

    r1, rec = rt.invoke("coordinator", step="coordinator_kickoff", index=1,
                        prompt=multi_nl.coordinator_kickoff_prompt(task),
                        prompt_inputs={"task_statement_verbatim": task.statement,
                                       "upstream_reports": [], "step": "coordinator_kickoff"})
    stop = rt.must_stop(r1, rec)
    if stop:
        return rt.abort("coordinator_kickoff", r1, stop)
    coordinator_session_id = r1.session_id
    investigation_instruction = r1.final_text
    s.record_handoff(index=1, sender="coordinator", recipient="investigator",
                     label="investigation_instruction", body=investigation_instruction)

    r2, rec = rt.invoke("investigator", step="investigate", index=2,
                        prompt=multi_nl.investigator_prompt(task, investigation_instruction),
                        prompt_inputs={"task_statement_verbatim": task.statement,
                                       "coordinator_instruction": investigation_instruction,
                                       "upstream_reports": [], "step": "investigate"})
    stop = rt.must_stop(r2, rec)
    if stop:
        return rt.abort("investigate", r2, stop)
    investigation_report = r2.final_text
    s.record_handoff(index=2, sender="investigator", recipient="coordinator",
                     label="investigation_report", body=investigation_report)

    r3, rec = rt.invoke("coordinator", step="coordinator_plan", index=3,
                        prompt=multi_nl.coordinator_after_investigation_prompt(investigation_report),
                        prompt_inputs={"investigator_report": investigation_report,
                                       "step": "coordinator_plan"},
                        resume=coordinator_session_id)
    stop = rt.must_stop(r3, rec)
    if stop:
        return rt.abort("coordinator_plan", r3, stop)
    implementation_instruction = r3.final_text
    s.record_handoff(index=3, sender="coordinator", recipient="implementer",
                     label="implementation_instruction", body=implementation_instruction)
    s.record_handoff(index=4, sender="coordinator", recipient="implementer",
                     label="forwarded_investigation_report", body=investigation_report)

    r4, rec = rt.invoke("implementer", step="implement", index=4,
                        prompt=multi_nl.implementer_prompt(
                            task, implementation_instruction, investigation_report),
                        prompt_inputs={"task_statement_verbatim": task.statement,
                                       "coordinator_instruction": implementation_instruction,
                                       "investigator_report": investigation_report,
                                       "step": "implement"})
    stop = rt.must_stop(r4, rec)
    if stop:
        return rt.abort("implement", r4, stop)
    implementation_report = r4.final_text
    s.record_handoff(index=5, sender="implementer", recipient="coordinator",
                     label="implementation_report", body=implementation_report)

    r5, rec = rt.invoke("coordinator", step="coordinator_wrapup", index=5,
                        prompt=multi_nl.coordinator_wrapup_prompt(implementation_report),
                        prompt_inputs={"implementer_report": implementation_report,
                                       "step": "coordinator_wrapup"},
                        resume=coordinator_session_id)
    if r5.ok and rec["verification"]["verified"]:
        s.record_handoff(index=6, sender="coordinator", recipient="user",
                         label="final_result", body=r5.final_text)
    return rt.finish()


def run(*, arm: str, task: Task, repeat_id: int, cfg: config.RunConfig,
        cli: config.ClaudeCli, capability_report: dict, runs_dir=None,
        phase: Optional[str] = None, difficulty: Optional[dict] = None) -> dict:
    """``phase`` ("calibration" / "evaluation", section 19) and ``difficulty``
    (the frozen stratum and labels hash) are run METADATA only: nothing here
    passes them to a prompt, a flag or the environment of a Claude process."""
    if arm not in config.STAGE2_CONFIGS:
        raise KeyError(f"not a Stage-2 configuration: {arm!r}")
    if phase is not None and phase not in config.STAGE2_PHASES:
        raise ValueError(f"unknown Stage-2 phase {phase!r}")
    if cfg.model != config.STRONG_MODEL:
        raise ValueError(
            "Stage-2 runs use the unchanged base configuration (model 'sonnet'); "
            "each role's model comes from the Stage-2 topology, never from --model.")
    env_guard = config.preflight_model_routing_env()
    prompts = check_prompt_sources()
    topology = config.stage2_topology(arm)
    session = start_run(task=task, arm=arm, repeat_id=repeat_id, cfg=cfg, cli=cli,
                        capability_report=capability_report, runs_dir=runs_dir,
                        topology=topology)
    # Completed before any Claude invocation: the workspace base commit exists now.
    session.metadata["stage2_identity"] = stage2_identity(arm, cfg, session)
    session.metadata["model_routing_env_guard"] = env_guard
    session.metadata["prompt_sources_checked"] = prompts
    session.metadata["stage2_phase"] = phase or "unassigned"
    if difficulty is not None:
        session.metadata["stage2_difficulty"] = dict(difficulty)
    (session.run_dir / "metadata.json").write_text(
        json.dumps(session.metadata, indent=2, sort_keys=True), encoding="utf-8")

    rt = _Router(session, topology)
    if topology["base_arm_topology"] == "A":
        return _run_single(rt, task)
    return _run_multi(rt, task)


class Stage2Arm:
    """runner.ARM_MODULES entry: `.run(...)` like arms/single.py and multi_nl.py."""

    def __init__(self, arm: str):
        self.ARM = arm

    def run(self, **kwargs) -> dict:
        return run(arm=self.ARM, **kwargs)


ARMS = {arm: Stage2Arm(arm) for arm in config.STAGE2_ARMS}
