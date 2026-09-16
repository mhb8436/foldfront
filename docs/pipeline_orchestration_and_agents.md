# Pipeline Orchestration and Evidence Agents

This note defines how `protein_pipeline` should describe orchestration and agents in the product, manuscript, and RFP materials. The key distinction is simple: the orchestrator executes the workflow; agents review evidence and suggest actions.

## 1. Three Operational Planes

### Control Plane: Pipeline Orchestrator

The orchestrator is deterministic. It validates inputs, resolves the stage graph, starts and stops stages, enforces safe partial reruns, writes status, and preserves artifacts under `PIPELINE_OUTPUT_ROOT/<run_id>/`.

Canonical stage order:

```text
msa -> rfd3 -> bioemu -> design -> soluprot -> af2 -> novelty
```

The orchestrator owns:

- stage ordering and dependency checks
- stop/resume/checkpoint state
- safe rerun and cache reuse rules
- run-scoped status and event records
- failure and recovery boundaries

### Model Execution Plane: Provider Registry

The model provider registry decides where each heavy model runs. A model can be connected to RunPod, a local GPU HTTP API, or disabled. The scientific pipeline should not care whether ProteinMPNN, RFD3, BioEmu, ColabFold, ESMFold, MMseqs2, or Relax is backed by serverless infrastructure or a local GPU server, as long as the provider writes the expected artifact contract.

This is the system-level basis for the manuscript claim that the platform is model-replaceable.

### Evidence Plane: Agent Review

Agents do not replace the pipeline graph. They read artifacts and emit structured verdicts that help users understand whether a stage should proceed, be monitored, or be recovered.

Recommended agent roles:

- **Structure Agent:** pLDDT, RMSD, chain mapping, clash or fold-consistency checks.
- **Sequence Agent:** MSA depth, conservation, fixed-position and ProteinMPNN diversity checks.
- **Stability/Solubility Agent:** SoluProt, BioEmu target RMSD, Relax score, candidate attrition.
- **Literature Constraint Agent:** PDF-derived residue and mutation-sensitive region suggestions.
- **Report Agent:** run-level summary and exportable interpretation.

Each agent verdict should be machine-readable:

```json
{
  "stage": "bioemu",
  "decision": "recover",
  "confidence": 0.82,
  "rationale": "BioEmu samples were generated, but target RMSD could not be computed for the expected chain.",
  "evidence": ["bioemu/summary.json", "events.jsonl"],
  "recommended_action": "normalize chain mapping or rerun BioEmu with explicit chain selection"
}
```

## 2. Orchestration Trace

Every run now emits `orchestration_trace.jsonl` next to the existing `events.jsonl` and `agent_panel.jsonl`.

The trace separates two event types:

- `stage_status`: deterministic control-plane status from the pipeline runner.
- `agent_verdict`: advisory evidence-plane verdict from the Agent Panel.

Example:

```json
{
  "kind": "orchestration_trace",
  "event_type": "stage_status",
  "plane": "control",
  "source": "pipeline_runner",
  "run_id": "admin_test1",
  "stage": "rfd3",
  "state": "completed",
  "detail": "cached"
}
```

```json
{
  "kind": "orchestration_trace",
  "event_type": "agent_verdict",
  "plane": "evidence",
  "source": "agent_panel",
  "run_id": "admin_test1",
  "stage": "bioemu",
  "decision": "recover",
  "confidence": 0.82,
  "error": "target RMSD unavailable",
  "recovery": {
    "attempted": true,
    "actions": ["normalize chain mapping"]
  }
}
```

This trace is useful for the paper because it makes the system auditable without overstating the agent role. It shows which stage ran, what the pipeline state was, and what the advisory agents concluded.

## 3. Recommended Manuscript Framing

Use this wording:

> The platform uses deterministic workflow orchestration combined with agent-assisted evidence review. Each model stage is executed through a provider-agnostic registry, while structured evidence agents evaluate intermediate artifacts, checkpoint decisions, and recovery recommendations.

Avoid this wording:

> The agent autonomously designs proteins and controls all pipeline decisions.

The current benchmark supports the first claim. It does not yet validate autonomous mutation-policy learning by an LLM agent.

## 4. Product Terminology

Use consistent terms in UI and documentation:

- **Pipeline Orchestrator:** backend execution engine.
- **Model Provider Registry:** RunPod/HTTP/disabled model connection layer.
- **Workflow Studio:** human-in-the-loop checkpoint and staged execution UI.
- **Evidence Agent Panel:** structured expert checks, recovery notes, and report links.
- **Context Copilot:** conversational help for using the current screen or interpreting a run.

## 5. Paper Figure Recommendation

For the manuscript, use a three-plane architecture figure:

1. **Control Plane:** Project/Round, Workflow Studio, orchestrator, checkpoint gate.
2. **Model Execution Plane:** provider registry connected to RunPod or GPU HTTP model services.
3. **Evidence Plane:** artifacts, metrics, evidence-agent verdicts, reports, and feedback records.

This figure will make the contribution clearer than a generic chatbot or autonomous-agent diagram.
