#!/usr/bin/env python3
"""Subprocess-isolated framework workers for BEAAA-13764.

The dispatcher owns reconciliation. This module owns the worker boundary:
dependency pins are checked inside a fresh Python process, and every outcome is
normalized to the WorkerAttemptResult JSON contract consumed by
api.full_scan_executor.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.metadata
import inspect as py_inspect
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.framework_worker_registry import WORKERS, WorkerDefinition
from scanners.eval_framework_venv import venv_python


WORKER_RESULT_SCHEMA = "framework-worker-attempt-result/v0.1"
PYTHON_PACKAGE_NAMES = {
    "inspect-ai",
    "pyrit",
    "agentdojo",
    "llm-guard",
    "presidio-analyzer",
    "presidio-anonymizer",
    "spacy",
    "transformers",
    "PyYAML",
}
CLI_PACKAGE_NAMES = {"promptfoo"}
SOURCE_PIN_PREFIXES = ("source-",)


class FrameworkWorkerBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class IsolatedFrameworkWorker:
    """Callable adapter used by the full-scan dispatcher.

    The object intentionally has the same call shape as
    api.full_scan_executor.FrameworkWorker. It does not import framework
    packages in the parent process; the child process performs dependency
    checks and emits a common JSON result.
    """

    worker_id: str
    timeout_seconds: int = 120

    def __call__(self, registry_row: Mapping[str, Any], job: Any) -> Any:
        payload = {
            "worker_id": self.worker_id,
            "registry_row": dict(registry_row),
            "job": {
                "job_id": job.job_id,
                "agent_id": job.agent_id,
                "scanner_version": job.scanner_version,
                "corpus_version": job.corpus_version,
                "model_versions": dict(job.model_versions),
                "seed": job.seed,
            },
        }
        proc = subprocess.run(
            [_worker_python(self.worker_id), "-m", "tools.framework_workers", "run"],
            input=json.dumps(payload, sort_keys=True),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            cwd=str(REPO_ROOT),
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            suffix = f":{detail[-1][:180]}" if detail else ""
            return _result(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason=f"worker_process_error:{self.worker_id}{suffix}",
                model_version=_primary_model_version(payload["job"]["model_versions"]),
            )
        try:
            decoded = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return _result(
                status="incomplete",
                evidence_ref=None,
                incomplete_reason=f"worker_invalid_json:{self.worker_id}:{exc.__class__.__name__}",
                model_version=_primary_model_version(payload["job"]["model_versions"]),
            )
        return _result(
            status=str(decoded.get("status") or "incomplete"),
            evidence_ref=decoded.get("evidence_ref"),
            incomplete_reason=decoded.get("incomplete_reason"),
            model_version=decoded.get("model_version"),
        )


def build_default_framework_workers(
    *,
    timeout_seconds: int = 120,
) -> dict[str, IsolatedFrameworkWorker]:
    return {
        worker_id: IsolatedFrameworkWorker(
            worker_id=worker_id,
            timeout_seconds=timeout_seconds,
        )
        for worker_id in WORKERS
    }


def run_worker_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    worker_id = str(payload.get("worker_id") or "")
    worker = WORKERS.get(worker_id)
    job = payload.get("job") if isinstance(payload.get("job"), Mapping) else {}
    registry_row = (
        payload.get("registry_row")
        if isinstance(payload.get("registry_row"), Mapping)
        else {}
    )
    model_version = _primary_model_version(job.get("model_versions", {}))
    if worker is None:
        return _result_dict(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason=f"worker_definition_missing:{worker_id}",
            model_version=model_version,
        )

    dependency_error = _dependency_error(worker)
    if dependency_error:
        return _result_dict(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason=f"worker_dependency_unavailable:{worker_id}:{dependency_error}",
            model_version=model_version,
        )

    if worker.worker_id == "inspect":
        return _inspect_adapter_result(worker, registry_row, job, model_version)
    if worker.worker_id == "pyrit":
        return _pyrit_adapter_result(worker, registry_row, job, model_version)
    if worker.worker_id == "agentdojo":
        return _agentdojo_adapter_result(worker, registry_row, job, model_version)
    if worker.worker_id == "garak":
        return _garak_adapter_result(worker, registry_row, job, model_version)
    return _adapter_result(worker, registry_row, job, model_version)


def _agentdojo_adapter_result(
    worker: WorkerDefinition,
    registry_row: Mapping[str, Any],
    job: Mapping[str, Any],
    model_version: str,
) -> dict[str, Any]:
    """Run one AgentDojo task against its native suite and end-state verifier."""
    subject = _subject_adapter(job, registry_row)
    if subject is None:
        return _unsupported_interface(worker, model_version, "missing_subject_adapter")
    suite_name = str(registry_row.get("agentdojo_suite") or "")
    task_id = str(registry_row.get("agentdojo_task_id") or "")
    task_kind = str(registry_row.get("agentdojo_task_kind") or "user")
    benchmark_version = str(registry_row.get("agentdojo_benchmark_version") or "v1.2.2")
    if not suite_name or not task_id or task_kind not in {"user", "injection"}:
        return _unsupported_interface(worker, model_version, "missing_or_invalid_suite_task")
    try:
        from agentdojo.task_suite.load_suites import get_suite  # type: ignore
    except ImportError as exc:
        return _result_dict(
            status="incomplete", evidence_ref=None,
            incomplete_reason=f"worker_dependency_unavailable:{worker.worker_id}:agentdojo={exc.__class__.__name__}",
            model_version=model_version,
        )
    try:
        from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement  # type: ignore
        from agentdojo.functions_runtime import FunctionCall  # type: ignore
        from agentdojo.types import text_content_block_from_string  # type: ignore

        suite = get_suite(benchmark_version, suite_name)
        if task_kind == "injection":
            task = suite.get_injection_task_by_id(task_id)
            user_task = next(iter(suite.user_tasks.values()))
            injection_task = task
        else:
            task = suite.get_user_task_by_id(task_id)
            user_task = task
            injection_task = None

        class BackBondSubjectPipeline(BasePipelineElement):  # type: ignore[misc]
            name = "backbond-worker-subject"

            def query(self, query: str, runtime: Any, env: Any, messages: Any = (), extra_args: Any = None) -> Any:
                response = _complete_subject(subject, query)
                calls = [
                    FunctionCall(
                        function=str(call.get("function") or call.get("name") or ""),
                        args=dict(call.get("args") or {}),
                        id=str(call.get("id")) if call.get("id") is not None else None,
                    )
                    for call in subject.get("tool_calls", ())
                    if isinstance(call, Mapping)
                ]
                for call in calls:
                    _, error = runtime.run_function(env, call.function, call.args, raise_on_error=False)
                    if error:
                        raise ValueError(f"subject_tool_call_failed:{call.function}:{error}")
                output = text_content_block_from_string(response)
                assistant = {"role": "assistant", "content": [output], "tool_calls": calls or None}
                return query, runtime, env, [*messages, assistant], dict(extra_args or {})

        passed, security = suite.run_task_with_pipeline(
            BackBondSubjectPipeline(),
            user_task,
            injection_task=injection_task,
            injections={},
        )
        passed = bool(passed and security)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return _unsupported_interface(worker, model_version, f"native_suite_interface:{exc.__class__.__name__}")
    except Exception as exc:
        return _result_dict(
            status="incomplete", evidence_ref=None,
            incomplete_reason=f"worker_native_invocation_error:{worker.worker_id}:{exc.__class__.__name__}",
            model_version=model_version,
        )
    return _result_dict(
        status="complete" if passed else "incomplete",
        evidence_ref=_evidence_ref(registry_row, job),
        incomplete_reason=None if passed else "agentdojo_terminal_verdict_failed",
        model_version=model_version,
    )


def _garak_adapter_result(
    worker: WorkerDefinition,
    registry_row: Mapping[str, Any],
    job: Mapping[str, Any],
    model_version: str,
) -> dict[str, Any]:
    """Invoke a pinned Garak Probe with a local target-backed Generator."""
    subject = _subject_adapter(job, registry_row)
    if subject is None:
        return _unsupported_interface(worker, model_version, "missing_subject_adapter")
    probe_name = str(registry_row.get("garak_probe") or "")
    if not probe_name:
        case_id = str(registry_row.get("case_id") or "")
        probe_name = case_id.rsplit(":", 1)[-1] if "." in case_id else ""
    if not probe_name:
        return _unsupported_interface(worker, model_version, "missing_probe_name")
    try:
        from garak import _config, _plugins  # type: ignore
        from garak.evaluators.base import ThresholdEvaluator  # type: ignore
        from garak.generators.base import Generator  # type: ignore
    except ImportError as exc:
        return _result_dict(
            status="incomplete", evidence_ref=None,
            incomplete_reason=f"worker_dependency_unavailable:{worker.worker_id}:garak={exc.__class__.__name__}",
            model_version=model_version,
        )

    class BackBondSubjectGenerator(Generator):  # type: ignore[misc]
        """Garak generator backed by the scanner's controlled target adapter."""

        generator_family_name = "backbond-worker-subject"
        parallel_capable = False

        def _call_model(self, prompt: str, generations_this_call: int = 1) -> list[str]:
            return [_complete_subject(subject, prompt) for _ in range(generations_this_call)]

    report = tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8")
    try:
        _config.load_base_config()
        _config.transient.reportfile = report
        _config.run.generations = 1
        _config.run.seed = int(job.get("seed") or 0)
        probe = _plugins.load_plugin(f"probes.{probe_name}")
        attempts = list(probe.probe(BackBondSubjectGenerator(model_version)))
        detector_names = [probe.primary_detector] if getattr(probe, "primary_detector", None) else list(probe.recommended_detector)
        if not detector_names:
            return _unsupported_interface(worker, model_version, "native_detector_unavailable")
        detector = _plugins.load_plugin(f"detectors.{detector_names[0]}")
        evaluator = ThresholdEvaluator(float(_config.run.eval_threshold))
        verdicts = [
            evaluator.test(float(score))
            for attempt in attempts
            for score in detector.detect(attempt)
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return _unsupported_interface(worker, model_version, f"native_probe_interface:{exc.__class__.__name__}")
    except Exception as exc:
        return _result_dict(
            status="incomplete", evidence_ref=None,
            incomplete_reason=f"worker_native_invocation_error:{worker.worker_id}:{exc.__class__.__name__}",
            model_version=model_version,
        )
    finally:
        report.close()
    passed = bool(verdicts) and all(verdicts)
    return _result_dict(
        status="complete" if passed else "incomplete",
        evidence_ref=_evidence_ref(registry_row, job),
        incomplete_reason=None if passed else "garak_terminal_verdict_failed",
        model_version=model_version,
    )


def _inspect_adapter_result(
    worker: WorkerDefinition,
    registry_row: Mapping[str, Any],
    job: Mapping[str, Any],
    model_version: str,
) -> dict[str, Any]:
    subject = _subject_adapter(job, registry_row)
    if subject is None:
        return _unsupported_interface(worker, model_version, "missing_subject_adapter")
    prompt = _prompt_for_case(registry_row)
    if not prompt:
        return _unsupported_interface(worker, model_version, "missing_prompt_or_action")
    try:
        import inspect_ai  # type: ignore
        from inspect_ai import Task  # type: ignore
        from inspect_ai.dataset import Sample  # type: ignore
        from inspect_ai.model import GenerateConfig, Model, ModelAPI, ModelOutput, modelapi  # type: ignore
        from inspect_ai.scorer import Score, Target, scorer  # type: ignore
        from inspect_ai.solver import generate  # type: ignore
    except ImportError as exc:
        return _result_dict(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason=f"worker_dependency_unavailable:{worker.worker_id}:inspect-ai={exc.__class__.__name__}",
            model_version=model_version,
        )

    @modelapi("backbond-worker-subject")
    class BackBondWorkerModelAPI(ModelAPI):  # type: ignore[misc]
        async def generate(self, input: list[Any], tools: list[Any], tool_choice: Any, config: Any) -> Any:
            request = _inspect_prompt_from_messages(input) or prompt
            response = _complete_subject(subject, request)
            return ModelOutput.from_content(model=model_version, content=response)

    @scorer(metrics=[])
    def exact_case_terminal() -> Any:
        async def score(state: Any, target: Target) -> Any:
            response = str(state.output.completion or "")
            expected = str(target.text or "")
            passed = bool(response) and (not expected or expected in response)
            return Score(value=1.0 if passed else 0.0, answer=response)

        return score

    log_dir = tempfile.mkdtemp(prefix="backbond-inspect-worker-")
    try:
        task = Task(
            dataset=[
                Sample(
                    input=prompt,
                    target=_expected_for_case(registry_row),
                    id=str(registry_row.get("case_id") or "inspect.case"),
                    metadata={"rubric_point_id": str(registry_row.get("risk_or_rubric_id") or registry_row.get("rubric_point_id") or "")},
                )
            ],
            solver=[generate()],
            scorer=exact_case_terminal(),
        )
        logs = inspect_ai.eval(
            task,
            model=Model(BackBondWorkerModelAPI("backbond-worker-subject"), GenerateConfig(temperature=0)),
            display="none",
            log_dir=log_dir,
            log_format="json",
            limit=1,
            max_samples=1,
            fail_on_error=False,
        )
    except Exception as exc:
        return _result_dict(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason=f"worker_native_invocation_error:{worker.worker_id}:{exc.__class__.__name__}",
            model_version=model_version,
        )
    passed = _inspect_terminal_passed(logs)
    if passed is None:
        status = "incomplete"
        incomplete_reason = "inspect_terminal_score_unavailable"
        if not logs or getattr(logs[0], "status", None) != "success":
            incomplete_reason = "inspect_eval_not_success"
    else:
        status = "complete" if passed else "incomplete"
        incomplete_reason = None if passed else "inspect_terminal_verdict_failed"
    return _result_dict(
        status=status,
        evidence_ref=_evidence_ref(registry_row, job) if status == "complete" else None,
        incomplete_reason=incomplete_reason,
        model_version=model_version,
    )


def _pyrit_adapter_result(
    worker: WorkerDefinition,
    registry_row: Mapping[str, Any],
    job: Mapping[str, Any],
    model_version: str,
) -> dict[str, Any]:
    subject = _subject_adapter(job, registry_row)
    if subject is None:
        return _unsupported_interface(worker, model_version, "missing_subject_adapter")
    prompt = _prompt_for_case(registry_row)
    if not prompt:
        return _unsupported_interface(worker, model_version, "missing_prompt_or_action")
    try:
        import pyrit  # type: ignore
    except ImportError as exc:
        return _result_dict(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason=f"worker_dependency_unavailable:{worker.worker_id}:pyrit={exc.__class__.__name__}",
            model_version=model_version,
        )

    orchestrator_cls = _resolve_pyrit_orchestrator(pyrit)
    if orchestrator_cls is None:
        return _unsupported_interface(worker, model_version, "orchestrator_unavailable")
    _initialize_pyrit_memory()
    target = _PyRITSubjectTarget(subject)
    orchestrator = _instantiate_pyrit_orchestrator(orchestrator_cls, target)
    if orchestrator is None:
        return _unsupported_interface(worker, model_version, "orchestrator_constructor_unsupported")
    try:
        response = _run_pyrit_orchestrator(orchestrator, prompt)
    except Exception as exc:
        return _result_dict(
            status="incomplete",
            evidence_ref=None,
            incomplete_reason=f"worker_native_invocation_error:{worker.worker_id}:{exc.__class__.__name__}",
            model_version=model_version,
        )
    if response is None:
        return _unsupported_interface(worker, model_version, "orchestrator_run_unsupported")
    expected = _expected_for_case(registry_row)
    passed = bool(str(response)) and (not expected or expected in str(response))
    return _result_dict(
        status="complete" if passed else "incomplete",
        evidence_ref=_evidence_ref(registry_row, job) if passed else None,
        incomplete_reason=None if passed else "pyrit_terminal_verdict_failed",
        model_version=model_version,
    )


class _PyRITSubjectTarget:
    def __init__(self, subject: Mapping[str, Any]) -> None:
        self.subject = subject
        self._max_requests_per_minute = None
        self._verbose = False

    def complete(self, prompt: str) -> str:
        return _complete_subject(self.subject, prompt)

    def _validate_request(self, *, prompt_request: Any) -> None:
        return None

    def get_identifier(self) -> dict[str, str]:
        return {"__type__": "BackBondWorkerSubjectTarget", "__module__": __name__}

    def send_prompt(self, prompt: str) -> str:
        return self.complete(prompt)

    async def send_prompt_async(self, prompt: str | None = None, **kwargs: Any) -> Any:
        prompt_request = kwargs.get("prompt_request")
        if prompt_request is not None:
            request = _pyrit_prompt_request_text(prompt_request)
            response = self.complete(request)
            return _pyrit_response_request(prompt_request, response)
        return self.complete(str(prompt or ""))


def _adapter_result(
    worker: WorkerDefinition,
    registry_row: Mapping[str, Any],
    job: Mapping[str, Any],
    model_version: str,
) -> dict[str, Any]:
    """Return the common contract for adapters not yet bound to live targets.

    BEAAA-13764's isolation layer must not silently exclude a row or fabricate a
    measured pass. Until a worker has a target-specific executable adapter, it
    returns a terminal incomplete evidence row with the same version and seed
    pins the measured adapter will use.
    """
    reason = f"worker_adapter_not_bound:{worker.worker_id}"
    if not registry_row.get("evidence_uri_template"):
        reason = f"{reason}:missing_evidence_uri_template"
    if not job.get("job_id"):
        reason = f"{reason}:missing_job_id"
    return _result_dict(
        status="incomplete",
        evidence_ref=None,
        incomplete_reason=reason,
        model_version=model_version,
    )


def _worker_python(worker_id: str) -> str:
    if worker_id in {"inspect", "pyrit", "agentdojo", "garak"}:
        py = venv_python(worker_id)
        if py.exists():
            return str(py)
    return sys.executable


def _unsupported_interface(worker: WorkerDefinition, model_version: str, reason: str) -> dict[str, Any]:
    return _result_dict(
        status="incomplete",
        evidence_ref=None,
        incomplete_reason=f"worker_unsupported_interface:{worker.worker_id}:{reason}",
        model_version=model_version,
    )


def _subject_adapter(job: Mapping[str, Any], registry_row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    adapter = job.get("subject_adapter")
    if not isinstance(adapter, Mapping):
        adapter = registry_row.get("subject_adapter")
    return adapter if isinstance(adapter, Mapping) else None


def _complete_subject(subject: Mapping[str, Any], prompt: str) -> str:
    responses = subject.get("responses")
    if isinstance(responses, Mapping) and prompt in responses:
        return str(responses[prompt])
    if "response" in subject:
        return str(subject["response"])
    return f"LOCAL_TARGET_RESPONSE prompt_sha256={_sha256(prompt)[:16]} status=terminal"


def _prompt_for_case(registry_row: Mapping[str, Any]) -> str:
    for key in ("prompt_or_action", "prompt", "input", "objective"):
        value = registry_row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _expected_for_case(registry_row: Mapping[str, Any]) -> str:
    for key in ("expected_response", "expected_behavior", "target", "verdict"):
        value = registry_row.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _evidence_ref(registry_row: Mapping[str, Any], job: Mapping[str, Any]) -> str:
    template = str(
        registry_row.get("evidence_uri_template")
        or "evidence://{scan_id}/{test_id}/{case_id}.json"
    )
    return template.format(
        scan_id=str(job.get("job_id") or "scan-unavailable"),
        test_id=str(registry_row.get("test_id") or "test-unavailable"),
        case_id=str(registry_row.get("case_id") or "case-unavailable"),
    )


def _inspect_prompt_from_messages(messages: list[Any]) -> str:
    for message in reversed(messages):
        content = getattr(message, "content", "")
        if isinstance(content, str) and content:
            return content
    return ""


def _inspect_terminal_passed(logs: Any) -> bool | None:
    if not logs or getattr(logs[0], "status", None) != "success":
        return None
    samples = getattr(logs[0], "samples", None) or []
    if not samples:
        return None
    scores = getattr(samples[0], "scores", None)
    values: list[Any] = []
    if isinstance(scores, Mapping):
        values.extend(scores.values())
    elif isinstance(scores, list):
        values.extend(scores)
    elif scores is not None:
        values.append(scores)
    for score in values:
        value = getattr(score, "value", score)
        if value is None:
            continue
        try:
            return float(value) >= 1.0
        except (TypeError, ValueError):
            continue
    return None


def _resolve_pyrit_orchestrator(pyrit_module: Any) -> Any | None:
    module_candidates = [
        ("pyrit", "PromptSendingOrchestrator"),
        ("pyrit.orchestrator", "PromptSendingOrchestrator"),
        ("pyrit.orchestrator.prompt_sending_orchestrator", "PromptSendingOrchestrator"),
        ("pyrit.orchestrator.single_turn", "PromptSendingOrchestrator"),
        ("pyrit.orchestrator.single_turn.prompt_sending_orchestrator", "PromptSendingOrchestrator"),
    ]
    for module_name, attr in module_candidates:
        try:
            module = pyrit_module if module_name == "pyrit" else importlib.import_module(module_name)
        except ImportError:
            continue
        current = getattr(module, attr, None)
        if current is not None:
            return current
    return None


def _pyrit_prompt_request_text(prompt_request: Any) -> str:
    pieces = getattr(prompt_request, "request_pieces", None) or []
    if pieces:
        piece = pieces[-1]
        for attr in ("converted_value", "original_value"):
            value = getattr(piece, attr, None)
            if value:
                return str(value)
    return str(prompt_request)


def _pyrit_response_request(prompt_request: Any, response: str) -> Any:
    pieces = getattr(prompt_request, "request_pieces", None) or []
    if not pieces:
        return response
    piece = pieces[-1]
    try:
        from pyrit.models import PromptRequestPiece, PromptRequestResponse  # type: ignore
    except ImportError:
        return response
    try:
        response_piece = PromptRequestPiece(
            role="assistant",
            original_value=response,
            conversation_id=getattr(piece, "conversation_id", None),
            sequence=int(getattr(piece, "sequence", 0) or 0) + 1,
            original_prompt_id=getattr(piece, "id", None),
        )
        return PromptRequestResponse(request_pieces=[response_piece])
    except Exception:
        return response


def _initialize_pyrit_memory() -> None:
    try:
        from pyrit.memory import CentralMemory, DuckDBMemory  # type: ignore
    except ImportError:
        return
    try:
        CentralMemory.get_memory_instance()
        return
    except Exception:
        pass
    db_path = Path(tempfile.mkdtemp(prefix="backbond-pyrit-memory-")) / "memory.duckdb"
    CentralMemory.set_memory_instance(DuckDBMemory(db_path=db_path))


def _instantiate_pyrit_orchestrator(orchestrator_cls: Any, target: _PyRITSubjectTarget) -> Any | None:
    for kwargs in (
        {"objective_target": target},
        {"prompt_target": target},
        {"target": target},
        {"objective_target": target, "adversarial_chat": None},
        {},
    ):
        try:
            return orchestrator_cls(**kwargs)
        except TypeError:
            continue
    return None


def _run_pyrit_orchestrator(orchestrator: Any, prompt: str) -> str | None:
    for method_name in (
        "send_prompts_async",
        "send_prompt_async",
        "run_attack",
        "send_prompt",
        "send_prompts",
        "run",
    ):
        method = getattr(orchestrator, method_name, None)
        if method is None:
            continue
        result = _invoke_pyrit_method(method, method_name, prompt)
        if py_inspect.isawaitable(result):
            result = asyncio.run(result)
        return _stringify_pyrit_result(result)
    return None


def _invoke_pyrit_method(method: Any, method_name: str, prompt: str) -> Any:
    if "prompts" not in method_name:
        return method(prompt)
    for kwargs in (
        {"prompt_list": [prompt]},
        {"prompts": [prompt]},
    ):
        try:
            return method(**kwargs)
        except TypeError:
            continue
    return method([prompt])


def _stringify_pyrit_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list) and result:
        return _stringify_pyrit_result(result[-1])
    for attr in ("response", "content", "answer", "output"):
        value = getattr(result, attr, None)
        if value:
            return str(value)
    return str(result)


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dependency_error(worker: WorkerDefinition) -> str | None:
    for name, expected in worker.dependency_pins.items():
        expected_s = str(expected)
        if expected_s.startswith(SOURCE_PIN_PREFIXES):
            continue
        if name.endswith(".py") or name.endswith(".txt") or "/" in name:
            if not (REPO_ROOT / name).exists():
                return f"{name}=missing-source"
            continue
        if name in CLI_PACKAGE_NAMES:
            if shutil.which(name) is None:
                return f"{name}=missing-cli"
            continue
        if name in {"tau-bench", "tau2-bench"}:
            # These are git pins in requirements/eval_pins.txt. The executable
            # adapter is blocked until the package exposes an importable module.
            return f"{name}=git-pin-not-importable"
        package_name = name if name in PYTHON_PACKAGE_NAMES else name
        try:
            installed = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            return f"{name}=missing-package"
        if installed != expected_s:
            return f"{name}=installed-{installed}-expected-{expected_s}"
    return None


def _result(
    *,
    status: str,
    evidence_ref: str | None,
    incomplete_reason: str | None,
    model_version: str | None,
) -> Any:
    from api.full_scan_executor import WorkerAttemptResult

    return WorkerAttemptResult(
        status=status,
        evidence_ref=evidence_ref,
        incomplete_reason=incomplete_reason,
        model_version=model_version,
    )


def _result_dict(
    *,
    status: str,
    evidence_ref: str | None,
    incomplete_reason: str | None,
    model_version: str | None,
) -> dict[str, Any]:
    return {
        "schema": WORKER_RESULT_SCHEMA,
        "status": status,
        "evidence_ref": evidence_ref,
        "incomplete_reason": incomplete_reason,
        "model_version": model_version,
    }


def _primary_model_version(model_versions: Mapping[str, Any]) -> str:
    primary = model_versions.get("primary")
    if isinstance(primary, str) and primary:
        return primary
    return "model-version-unavailable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    args = parser.parse_args(argv)
    if args.command == "run":
        payload = json.loads(sys.stdin.read() or "{}")
        result = run_worker_payload(payload)
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
