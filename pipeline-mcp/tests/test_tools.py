import json
import re
import subprocess
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from pipeline_mcp.models import PipelineRequest
from pipeline_mcp.pipeline import PipelineRunner
from pipeline_mcp.storage import init_run
from pipeline_mcp.storage import list_runs
from pipeline_mcp.storage import set_status
from pipeline_mcp.tools import ToolDispatcher
from pipeline_mcp.tools import AutoRetryConfig
from pipeline_mcp.tools import _run_with_auto_retry
from pipeline_mcp.tools import _build_comparison_summary
from pipeline_mcp.tools import _build_hit_list_rows
from pipeline_mcp.tools import tool_definitions
from pipeline_mcp.tools import pipeline_request_from_args


@contextmanager
def _tmpdir():
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"run_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    yield str(path)


class TestTools(unittest.TestCase):
    def test_frontend_cath_tab_is_standalone_pipeline_ui(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        index_html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
        app_js = (repo_root / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-tab="cath"', index_html)
        tabs_match = re.search(r"const TAB_OPTIONS = \[(.*?)\];", app_js, re.DOTALL)
        self.assertIsNotNone(tabs_match)
        self.assertIn('"cath"', tabs_match.group(1))
        self.assertNotIn("pipeline.cath_launch_training", app_js)
        self.assertNotIn("cathLaunchTrainBtn", app_js)
        self.assertNotIn("cathTrainingSubsets", app_js)

    def test_frontend_cath_launch_buttons_survive_empty_overview(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_js = (repo_root / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('const order = ["train", "val", "test"];', app_js)
        self.assertIn("? overview.subsets", app_js)
        self.assertIn(": {};", app_js)
        self.assertNotIn('if (!subsets) {', app_js)
        self.assertIn('data-cath-launch="${escapeHtml(subset)}"', app_js)

    def test_frontend_run_search_is_wired_to_backend_query(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        index_html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")
        app_js = (repo_root / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="runSearchInput"', index_html)
        self.assertIn('id="analyzeRunSearchInput"', index_html)
        self.assertIn("scheduleRunSearchRefresh", app_js)
        self.assertIn("query,", app_js)
        self.assertIn("include_subruns: false", app_js)
        self.assertIn("include_cath: false", app_js)

    def test_frontend_cath_keep_local_is_opt_in(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        index_html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")

        match = re.search(r'<input id="cathKeepLocal"[^>]*>', index_html)
        self.assertIsNotNone(match)
        self.assertNotIn("checked", match.group(0))

    def test_list_runs_hides_internal_evolution_and_cath_runs_by_default(self) -> None:
        with _tmpdir() as tmp:
            visible = [
                "admin_20260430_064926_afb67369",
                "pys74631_kribb.re.kr_ev_3rgk",
            ]
            hidden = [
                ".evolution_orphans",
                "admin_20260430_064926_afb67369_round1_pool",
                "admin_20260430_064926_afb67369_r1_top_k_rfd3_spec-1_0_model_4_82",
                "pys74631_kribb.re.kr_ev_3rgk_r1_train_rfd3_spec-1_0_model_8_59",
                "cath_test_1b65A00",
            ]
            for run_id in visible + hidden:
                init_run(tmp, run_id)

            self.assertEqual(set(list_runs(tmp, limit=20)), set(visible))
            self.assertIn(
                hidden[1],
                list_runs(tmp, limit=20, include_subruns=True),
            )
            self.assertIn(
                hidden[-1],
                list_runs(tmp, limit=20, include_cath=True),
            )

    def test_list_runs_query_searches_before_limit(self) -> None:
        with _tmpdir() as tmp:
            for idx in range(80):
                init_run(tmp, f"admin_old_{idx:02d}")
            init_run(tmp, "admin_target_needle")

            self.assertEqual(
                list_runs(tmp, limit=1, query="needle"),
                ["admin_target_needle"],
            )

    def test_frontend_fasta_download_uses_valid_template_literals(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_js = (repo_root / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const fasta = `>${seqId}\\n${sequence}\\n`;", app_js)
        self.assertIn("downloadTextFile(`${seqId}.fasta`, fasta);", app_js)
        self.assertNotIn(r"const fasta = \`>\${seqId}\\n\${sequence}\\n\`;", app_js)

    def test_app_module_imports_without_python_dotenv(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = """
import builtins

real_import = builtins.__import__

def blocked(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "dotenv":
        raise ModuleNotFoundError("No module named 'dotenv'")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = blocked
import pipeline_mcp.app
print("ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            env={"PYTHONPATH": str(repo_root / "src")},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_tool_definitions_expose_project_round_ids_on_run_schemas(self) -> None:
        defs = {tool["name"]: tool for tool in tool_definitions()}
        run_props = defs["pipeline.run"]["inputSchema"]["properties"]
        prompt_props = defs["pipeline.run_from_prompt"]["inputSchema"]["properties"]

        self.assertEqual(run_props["project_id"]["type"], "string")
        self.assertEqual(run_props["round_id"]["type"], "string")
        self.assertEqual(prompt_props["project_id"]["type"], "string")
        self.assertEqual(prompt_props["round_id"]["type"], "string")

    def test_pipeline_request_from_args_accepts_project_and_round_ids(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "project_id": "tev_campaign",
                "round_id": "round_01",
            }
        )
        self.assertEqual(req.project_id, "tev_campaign")
        self.assertEqual(req.round_id, "round_01")

    def test_pipeline_request_from_args_preserves_evolution_advanced_options(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "evolution_mode": True,
                "evolution_label_source": "in_silico_af2",
                "evolution_surrogate_model": "xgboost",
                "use_memory_bank": True,
                "evolution_objective_metric": "soluble_yield",
                "evolution_experiment_source_run_id": "round_1",
            }
        )

        self.assertEqual(req.evolution_label_source, "in_silico_af2")
        self.assertEqual(req.evolution_surrogate_model, "xgboost")
        self.assertEqual(req.evolution_objective_metric, "soluble_yield")
        self.assertEqual(req.evolution_experiment_source_run_id, "round_1")
        self.assertTrue(req.use_memory_bank)

    def test_pipeline_run_schema_advertises_supported_evolution_surrogates(self) -> None:
        defs = {tool["name"]: tool for tool in tool_definitions()}
        props = defs["pipeline.run"]["inputSchema"]["properties"]
        prop = props["evolution_surrogate_model"]

        self.assertEqual(prop["default"], "rf")
        self.assertEqual(
            prop["enum"], ["rf", "ridge", "lightgbm", "xgboost", "ensemble"]
        )
        self.assertEqual(props["evolution_label_source"]["default"], "experimental")
        self.assertEqual(
            props["evolution_label_source"]["enum"],
            ["experimental", "in_silico_af2"],
        )
        self.assertEqual(props["evolution_objective_metric"]["default"], "activity")
        self.assertEqual(props["evolution_experiment_source_run_id"]["type"], "string")

    def test_pipeline_run_schema_advertises_surrogate_triage_mode(self) -> None:
        defs = {tool["name"]: tool for tool in tool_definitions()}
        props = defs["pipeline.run"]["inputSchema"]["properties"]

        self.assertEqual(props["surrogate_triage_enabled"]["type"], "boolean")
        self.assertEqual(props["surrogate_triage_scope"]["default"], "per_tier")
        self.assertEqual(
            props["surrogate_triage_scope"]["enum"], ["per_tier", "pooled_tiers"]
        )
        self.assertEqual(props["surrogate_triage_initial_samples"]["default"], 30)
        self.assertEqual(props["surrogate_triage_top_k"]["default"], 20)
        model_schema = props["surrogate_triage_model"]["oneOf"][0]
        self.assertEqual(
            model_schema["enum"],
            ["auto", "rf", "ridge", "lightgbm", "xgboost", "ensemble"],
        )
        self.assertEqual(
            props["surrogate_triage_comparator_models"]["default"],
            ["rf", "ridge", "lightgbm", "xgboost"],
        )
        self.assertEqual(
            props["surrogate_triage_ensemble_models"]["default"],
            [],
        )
        self.assertEqual(props["surrogate_triage_cv_folds"]["default"], 5)

    def test_tool_definitions_include_cath_ops_tools(self) -> None:
        defs = {tool["name"]: tool for tool in tool_definitions()}
        expected = {
            "pipeline.cath_get_batch_overview",
            "pipeline.cath_launch_batch",
            "pipeline.cath_launch_training",
            "pipeline.cath_list_jobs",
            "pipeline.cath_get_job",
            "pipeline.cath_read_job_log",
            "pipeline.cath_stop_job",
            "pipeline.cath_delete_job",
        }
        self.assertTrue(expected.issubset(defs.keys()))

    def test_delete_managed_job_removes_only_job_record(self) -> None:
        from pipeline_mcp.cath_ops import delete_managed_job
        from pipeline_mcp.cath_ops import list_managed_jobs

        with _tmpdir() as tmp:
            workspace = Path(tmp)
            outputs_root = workspace / "outputs"
            job_root = workspace / "_ops" / "jobs" / "cath_batch_20260506_deadbeef"
            output_run = outputs_root / "abl_be_example"
            job_root.mkdir(parents=True)
            output_run.mkdir(parents=True)
            (job_root / "job.json").write_text(
                json.dumps(
                    {
                        "job_id": "cath_batch_20260506_deadbeef",
                        "kind": "cath_batch",
                        "state": "cancelled",
                        "created_at": "2026-05-06T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            (output_run / "status.json").write_text(
                json.dumps({"run_id": "abl_be_example", "state": "completed"}),
                encoding="utf-8",
            )

            result = delete_managed_job(str(outputs_root), "cath_batch_20260506_deadbeef")

            self.assertTrue(result["deleted"])
            self.assertFalse(job_root.exists())
            self.assertTrue(output_run.exists())
            self.assertEqual(list_managed_jobs(str(outputs_root), limit=20), [])

    def test_list_managed_jobs_backfills_missing_job_id_from_directory(self) -> None:
        from pipeline_mcp.cath_ops import list_managed_jobs

        with _tmpdir() as tmp:
            workspace = Path(tmp)
            outputs_root = workspace / "outputs"
            job_root = workspace / "_ops" / "jobs" / "legacy_cancelled_job"
            job_root.mkdir(parents=True)
            (job_root / "job.json").write_text(
                json.dumps(
                    {
                        "kind": "cath_batch",
                        "state": "cancelled",
                        "created_at": "2026-05-06T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            jobs = list_managed_jobs(str(outputs_root), limit=20)

            self.assertEqual(jobs[0]["job_id"], "legacy_cancelled_job")

    def test_cath_batch_overview_reports_completed_running_and_failed_counts(self) -> None:
        with _tmpdir() as tmp:
            workspace = Path(tmp)
            outputs_root = workspace / "outputs"
            outputs_root.mkdir(parents=True, exist_ok=True)
            (workspace / "cath_test").mkdir(parents=True, exist_ok=True)
            (workspace / "cath_test" / "1abcA00.pdb").write_text("END\n", encoding="utf-8")
            (workspace / "cath_test" / "2defA00.pdb").write_text("END\n", encoding="utf-8")
            (workspace / "cath_test" / "3ghiA00.pdb").write_text("END\n", encoding="utf-8")
            (workspace / "batch_success_test.csv").write_text(
                "timestamp,run_id\n2026-04-21T00:00:00Z,cath_test_1abcA00\n",
                encoding="utf-8",
            )
            (workspace / "batch_failed_test.csv").write_text(
                "timestamp,run_id,error\n2026-04-21T00:01:00Z,cath_test_2defA00,boom\n",
                encoding="utf-8",
            )

            run_id = "cath_test_3ghiA00"
            run_paths = init_run(str(outputs_root), run_id)
            set_status(
                run_paths,
                stage="af2_30",
                state="running",
                detail="predicting",
            )

            runner = PipelineRunner(output_root=str(outputs_root), mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            overview = dispatcher.call_tool("pipeline.cath_get_batch_overview", {"item_limit": 10})

            totals = overview.get("totals") or {}
            test_subset = (overview.get("subsets") or {}).get("test") or {}
            self.assertEqual(int(totals.get("total") or 0), 3)
            self.assertEqual(int(test_subset.get("counts", {}).get("completed") or 0), 1)
            self.assertEqual(int(test_subset.get("counts", {}).get("failed") or 0), 1)
            self.assertEqual(int(test_subset.get("counts", {}).get("running") or 0), 1)

    def test_cath_batch_overview_marks_cancelled_batch_running_status_as_stopped(self) -> None:
        with _tmpdir() as tmp:
            workspace = Path(tmp)
            outputs_root = workspace / "outputs"
            outputs_root.mkdir(parents=True, exist_ok=True)
            (workspace / "cath_test").mkdir(parents=True, exist_ok=True)
            (workspace / "cath_test" / "1abcA00.pdb").write_text("END\n", encoding="utf-8")

            run_id = "cath_test_1abcA00"
            run_paths = init_run(str(outputs_root), run_id)
            set_status(
                run_paths,
                stage="relax_50",
                state="running",
                detail="waiting for relax",
            )

            job_root = workspace / "_ops" / "jobs" / "cath_batch_cancelled"
            job_root.mkdir(parents=True, exist_ok=True)
            (job_root / "job.json").write_text(
                json.dumps(
                    {
                        "job_id": "cath_batch_cancelled",
                        "kind": "cath_batch",
                        "label": "CATH batch (test)",
                        "state": "cancelled",
                        "created_at": "2026-04-30T00:00:00Z",
                        "finished_at": "2026-04-30T01:00:00Z",
                        "return_code": -15,
                        "metadata": {"subset": "test"},
                    }
                ),
                encoding="utf-8",
            )

            runner = PipelineRunner(output_root=str(outputs_root), mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            overview = dispatcher.call_tool("pipeline.cath_get_batch_overview", {"item_limit": 10})

            test_subset = (overview.get("subsets") or {}).get("test") or {}
            counts = test_subset.get("counts", {})
            self.assertEqual(int(counts.get("running") or 0), 0)
            self.assertEqual(int(counts.get("stopped") or 0), 1)
            item = (test_subset.get("items") or [])[0]
            self.assertEqual(item.get("state"), "stopped")
            self.assertEqual(item.get("stage"), "relax_50")
            self.assertIn("cancelled", str(item.get("detail") or ""))

    def test_frontend_cath_grid_displays_stopped_count(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_js = (repo_root / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('["cath.count.stopped", counts.stopped]', app_js)
        self.assertIn('"cath.count.stopped": "Stopped"', app_js)
        self.assertIn('"cath.count.stopped": "정지"', app_js)

    def test_project_and_round_tools_enforce_owner_scope(self) -> None:
        owner = {"username": "hana", "run_prefix": "hana", "role": "user"}
        foreign = {"username": "minsu", "run_prefix": "minsu", "role": "user"}
        admin = {"username": "admin", "run_prefix": "admin", "role": "admin"}
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)

            project = dispatcher.call_tool(
                "pipeline.save_project",
                {
                    "project_id": "tev_campaign",
                    "name": "TEV campaign",
                    "description": "stability round-tracking",
                    "user": owner,
                },
            )
            self.assertEqual(str(project.get("project", {}).get("project_id") or ""), "tev_campaign")
            self.assertEqual(str(project.get("project", {}).get("owner_username") or ""), "hana")

            round_saved = dispatcher.call_tool(
                "pipeline.save_round",
                {
                    "project_id": "tev_campaign",
                    "round_id": "round_01",
                    "title": "Round 01",
                    "goal": "baseline stability screen",
                    "next_round_notes": "retest top 5 with tighter solubility gate",
                    "user": owner,
                },
            )
            self.assertEqual(str(round_saved.get("round", {}).get("round_id") or ""), "round_01")
            self.assertEqual(str(round_saved.get("round", {}).get("owner_username") or ""), "hana")
            self.assertEqual(
                str(round_saved.get("round", {}).get("next_round_notes") or ""),
                "retest top 5 with tighter solubility gate",
            )

            owned_projects = dispatcher.call_tool("pipeline.list_projects", {"user": owner})
            self.assertEqual(len(owned_projects.get("projects") or []), 1)

            foreign_projects = dispatcher.call_tool("pipeline.list_projects", {"user": foreign})
            self.assertEqual(len(foreign_projects.get("projects") or []), 0)

            admin_projects = dispatcher.call_tool("pipeline.list_projects", {"user": admin})
            self.assertEqual(len(admin_projects.get("projects") or []), 1)

            owned_rounds = dispatcher.call_tool(
                "pipeline.list_rounds",
                {"project_id": "tev_campaign", "user": owner},
            )
            self.assertEqual(len(owned_rounds.get("rounds") or []), 1)

            foreign_rounds = dispatcher.call_tool(
                "pipeline.list_rounds",
                {"project_id": "tev_campaign", "user": foreign},
            )
            self.assertEqual(len(foreign_rounds.get("rounds") or []), 0)

            with self.assertRaisesRegex(ValueError, "not allowed"):
                dispatcher.call_tool(
                    "pipeline.get_project",
                    {"project_id": "tev_campaign", "user": foreign},
                )

            with self.assertRaisesRegex(ValueError, "not allowed"):
                dispatcher.call_tool(
                    "pipeline.save_round",
                    {
                        "project_id": "tev_campaign",
                        "round_id": "round_02",
                        "title": "Round 02",
                        "user": foreign,
                    },
                )

    def test_archive_and_delete_rounds_projects_respect_owner_scope_and_visibility(self) -> None:
        owner = {"username": "hana", "run_prefix": "hana", "role": "user"}
        foreign = {"username": "minsu", "run_prefix": "minsu", "role": "user"}
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)

            dispatcher.call_tool(
                "pipeline.save_project",
                {
                    "project_id": "tev_campaign",
                    "name": "TEV campaign",
                    "user": owner,
                },
            )
            dispatcher.call_tool(
                "pipeline.save_round",
                {
                    "project_id": "tev_campaign",
                    "round_id": "round_01",
                    "title": "Round 01",
                    "user": owner,
                },
            )

            with self.assertRaisesRegex(ValueError, "not allowed"):
                dispatcher.call_tool(
                    "pipeline.archive_round",
                    {"project_id": "tev_campaign", "round_id": "round_01", "user": foreign},
                )

            archived_round = dispatcher.call_tool(
                "pipeline.archive_round",
                {"project_id": "tev_campaign", "round_id": "round_01", "user": owner},
            )
            self.assertEqual(str(archived_round.get("round", {}).get("status") or ""), "archived")

            visible_rounds = dispatcher.call_tool(
                "pipeline.list_rounds",
                {"project_id": "tev_campaign", "user": owner},
            ).get("rounds") or []
            self.assertEqual(len(visible_rounds), 0)

            archived_rounds = dispatcher.call_tool(
                "pipeline.list_rounds",
                {"project_id": "tev_campaign", "user": owner, "include_archived": True},
            ).get("rounds") or []
            self.assertEqual(len(archived_rounds), 1)

            restored_round = dispatcher.call_tool(
                "pipeline.restore_round",
                {"project_id": "tev_campaign", "round_id": "round_01", "user": owner},
            )
            self.assertEqual(str(restored_round.get("round", {}).get("status") or ""), "active")

            visible_rounds_after_restore = dispatcher.call_tool(
                "pipeline.list_rounds",
                {"project_id": "tev_campaign", "user": owner},
            ).get("rounds") or []
            self.assertEqual(len(visible_rounds_after_restore), 1)

            deleted_round = dispatcher.call_tool(
                "pipeline.delete_round",
                {"project_id": "tev_campaign", "round_id": "round_01", "user": owner},
            )
            self.assertEqual(bool(deleted_round.get("deleted")), True)
            self.assertFalse((Path(tmp) / "_workspace" / "projects" / "tev_campaign" / "rounds" / "round_01.json").exists())

            dispatcher.call_tool(
                "pipeline.save_round",
                {
                    "project_id": "tev_campaign",
                    "round_id": "round_02",
                    "title": "Round 02",
                    "user": owner,
                },
            )
            with self.assertRaisesRegex(ValueError, "delete_rounds=true"):
                dispatcher.call_tool(
                    "pipeline.delete_project",
                    {"project_id": "tev_campaign", "user": owner},
                )

            archived_project = dispatcher.call_tool(
                "pipeline.archive_project",
                {"project_id": "tev_campaign", "user": owner},
            )
            self.assertEqual(str(archived_project.get("project", {}).get("status") or ""), "archived")

            visible_projects = dispatcher.call_tool("pipeline.list_projects", {"user": owner}).get("projects") or []
            self.assertEqual(len(visible_projects), 0)

            archived_projects = dispatcher.call_tool(
                "pipeline.list_projects",
                {"user": owner, "include_archived": True},
            ).get("projects") or []
            self.assertEqual(len(archived_projects), 1)

            restored_project = dispatcher.call_tool(
                "pipeline.restore_project",
                {"project_id": "tev_campaign", "user": owner},
            )
            self.assertEqual(str(restored_project.get("project", {}).get("status") or ""), "active")

            visible_projects_after_restore = dispatcher.call_tool("pipeline.list_projects", {"user": owner}).get("projects") or []
            self.assertEqual(len(visible_projects_after_restore), 1)

            deleted_project = dispatcher.call_tool(
                "pipeline.delete_project",
                {"project_id": "tev_campaign", "delete_rounds": True, "user": owner},
            )
            self.assertEqual(bool(deleted_project.get("deleted")), True)
            self.assertFalse((Path(tmp) / "_workspace" / "projects" / "tev_campaign").exists())

    def test_save_project_and_round_generate_unique_ids_for_localized_names(self) -> None:
        owner = {"username": "hana", "run_prefix": "hana", "role": "user"}
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)

            project_a = dispatcher.call_tool(
                "pipeline.save_project",
                {
                    "name": "새 프로젝트",
                    "user": owner,
                },
            )["project"]
            project_b = dispatcher.call_tool(
                "pipeline.save_project",
                {
                    "name": "프로젝트",
                    "user": owner,
                },
            )["project"]

            self.assertNotEqual(str(project_a.get("project_id") or ""), str(project_b.get("project_id") or ""))
            self.assertNotEqual(str(project_a.get("project_id") or ""), "id")
            self.assertNotEqual(str(project_b.get("project_id") or ""), "id")

            listed_projects = dispatcher.call_tool("pipeline.list_projects", {"user": owner, "limit": 20}).get("projects") or []
            self.assertEqual(len(listed_projects), 2)

            round_a = dispatcher.call_tool(
                "pipeline.save_round",
                {
                    "project_id": str(project_a.get("project_id") or ""),
                    "title": "라운드",
                    "user": owner,
                },
            )["round"]
            round_b = dispatcher.call_tool(
                "pipeline.save_round",
                {
                    "project_id": str(project_a.get("project_id") or ""),
                    "title": "라운드",
                    "user": owner,
                },
            )["round"]

            self.assertNotEqual(str(round_a.get("round_id") or ""), str(round_b.get("round_id") or ""))
            self.assertNotEqual(str(round_a.get("round_id") or ""), "id")
            self.assertNotEqual(str(round_b.get("round_id") or ""), "id")

            listed_rounds = dispatcher.call_tool(
                "pipeline.list_rounds",
                {"project_id": str(project_a.get("project_id") or ""), "user": owner, "limit": 20},
            ).get("rounds") or []
            self.assertEqual(len(listed_rounds), 2)

    def test_pipeline_run_enforces_round_owner_scope_and_links_run(self) -> None:
        owner = {"username": "hana", "run_prefix": "hana", "role": "user"}
        foreign = {"username": "minsu", "run_prefix": "minsu", "role": "user"}
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            dispatcher.call_tool(
                "pipeline.save_project",
                {
                    "project_id": "tev_campaign",
                    "name": "TEV campaign",
                    "description": "stability round-tracking",
                    "user": owner,
                },
            )
            dispatcher.call_tool(
                "pipeline.save_round",
                {
                    "project_id": "tev_campaign",
                    "round_id": "round_01",
                    "title": "Round 01",
                    "goal": "baseline stability screen",
                    "user": owner,
                },
            )

            with self.assertRaisesRegex(ValueError, "not allowed"):
                dispatcher.call_tool(
                    "pipeline.run",
                    {
                        "target_fasta": fasta,
                        "dry_run": True,
                        "num_seq_per_tier": 1,
                        "conservation_tiers": [0.3],
                        "project_id": "tev_campaign",
                        "round_id": "round_01",
                        "user": foreign,
                    },
                )

            out = dispatcher.call_tool(
                "pipeline.run",
                {
                    "target_fasta": fasta,
                    "dry_run": True,
                    "num_seq_per_tier": 1,
                    "conservation_tiers": [0.3],
                    "project_id": "tev_campaign",
                    "round_id": "round_01",
                    "user": owner,
                },
            )
            round_path = (
                Path(tmp)
                / "_workspace"
                / "projects"
                / "tev_campaign"
                / "rounds"
                / "round_01.json"
            )
            record = json.loads(round_path.read_text(encoding="utf-8"))
            self.assertEqual(record.get("linked_run_ids"), [str(out.get("run_id") or "")])

    def test_pipeline_preflight_enforces_round_owner_scope(self) -> None:
        owner = {"username": "hana", "run_prefix": "hana", "role": "user"}
        foreign = {"username": "minsu", "run_prefix": "minsu", "role": "user"}
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            dispatcher.call_tool(
                "pipeline.save_project",
                {
                    "project_id": "tev_campaign",
                    "name": "TEV campaign",
                    "description": "stability round-tracking",
                    "user": owner,
                },
            )
            dispatcher.call_tool(
                "pipeline.save_round",
                {
                    "project_id": "tev_campaign",
                    "round_id": "round_01",
                    "title": "Round 01",
                    "goal": "baseline stability screen",
                    "user": owner,
                },
            )

            with self.assertRaisesRegex(ValueError, "not allowed"):
                dispatcher.call_tool(
                    "pipeline.preflight",
                    {
                        "target_fasta": fasta,
                        "project_id": "tev_campaign",
                        "round_id": "round_01",
                        "user": foreign,
                    },
                )

            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "target_fasta": fasta,
                    "project_id": "tev_campaign",
                    "round_id": "round_01",
                    "user": owner,
                },
            )
            self.assertIsInstance(out, dict)
            self.assertIn("ok", out)

    def test_pipeline_run_tool_dry_run(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {
                    "target_fasta": fasta,
                    "target_pdb": pdb,
                    "dry_run": True,
                    "num_seq_per_tier": 2,
                    "conservation_tiers": [0.3],
                    "fixed_positions_extra": {"A": [9]},
                },
            )
            self.assertIn("run_id", out)
            self.assertIn("output_dir", out)
            json.dumps(out)

    def test_pipeline_run_tool_dry_run_without_pdb(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {"target_fasta": fasta, "dry_run": True, "num_seq_per_tier": 2, "conservation_tiers": [0.3]},
            )
            self.assertIn("run_id", out)
            self.assertIn("output_dir", out)
            json.dumps(out)

    def test_pipeline_run_novelty_stage_wt_based_without_mmseqs_client(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {
                    "target_fasta": fasta,
                    "dry_run": True,
                    "stop_after": "novelty",
                    "novelty_enabled": True,
                    "num_seq_per_tier": 1,
                    "conservation_tiers": [0.3],
                    "soluprot_cutoff": 0.0,
                    "af2_plddt_cutoff": 0.0,
                    "af2_rmsd_cutoff": 999.0,
                },
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            status = dispatcher.call_tool("pipeline.read_artifact", {"run_id": run_id, "path": "status.json"})
            status_text = str(status.get("text") or "")
            self.assertIn('"state": "completed"', status_text)
            summary = dispatcher.call_tool("pipeline.read_artifact", {"run_id": run_id, "path": "summary.json"})
            summary_text = str(summary.get("text") or "")
            self.assertNotIn("MMseqs client is not configured", summary_text)

    def test_pipeline_run_tool_respects_run_id(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {"target_fasta": fasta, "target_pdb": pdb, "dry_run": True, "run_id": "my_test_run"},
            )
            self.assertEqual(Path(str(out.get("output_dir") or "")).name, "my_test_run")

    def test_pipeline_list_artifacts_keeps_root_input_snapshot_but_hides_internal_original_pdb(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A  -1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   1       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {
                    "target_fasta": "",
                    "target_pdb": pdb,
                    "dry_run": True,
                    "num_seq_per_tier": 1,
                    "conservation_tiers": [0.3],
                    "pdb_strip_nonpositive_resseq": True,
                    "pdb_renumber_resseq_from_1": True,
                },
            )
            output_dir = Path(str(out.get("output_dir") or ""))
            self.assertTrue((output_dir / "target.original.pdb").exists())
            internal_original = output_dir / "backbones" / "demo" / "target.original.pdb"
            internal_original.parent.mkdir(parents=True, exist_ok=True)
            internal_original.write_text("END\n", encoding="utf-8")
            listed = dispatcher.call_tool(
                "pipeline.list_artifacts",
                {"run_id": output_dir.name, "max_depth": 3, "limit": 200},
            )
            paths = {str(item.get("path") or "") for item in (listed.get("artifacts") or []) if isinstance(item, dict)}
            self.assertIn("target.pdb", paths)
            self.assertIn("target.original.pdb", paths)
            self.assertNotIn("backbones/demo/target.original.pdb", paths)

    def test_pipeline_list_artifacts_prioritizes_wt_and_workflow_session_when_limit_truncates(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            run_id = "artifact_priority_case"
            root = init_run(tmp, run_id).root

            (root / "target.original.pdb").write_text("END\n", encoding="utf-8")
            (root / "target.pdb").write_text("END\n", encoding="utf-8")
            (root / "workflow_studio" / "session.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "workflow_studio" / "session.json").write_text("{}\n", encoding="utf-8")
            (root / "wt" / "af2").mkdir(parents=True, exist_ok=True)
            (root / "wt" / "af2" / "ranked_0.pdb").write_text("END\n", encoding="utf-8")
            (root / "wt" / "af2" / "metrics.json").write_text("{}\n", encoding="utf-8")
            (root / "backbones" / "bioemu_topology").mkdir(parents=True, exist_ok=True)
            (root / "backbones" / "bioemu_topology" / "target.pdb").write_text("END\n", encoding="utf-8")
            (root / "backbones" / "rfd3_spec-1_0_model_0").mkdir(parents=True, exist_ok=True)
            (root / "backbones" / "rfd3_spec-1_0_model_0" / "target.pdb").write_text("END\n", encoding="utf-8")

            for idx in range(140):
                candidate_dir = root / "tiers" / "50" / "af2" / f"candidate_{idx:03d}"
                candidate_dir.mkdir(parents=True, exist_ok=True)
                (candidate_dir / "metrics.json").write_text("{}\n", encoding="utf-8")
                (candidate_dir / "ranked_0.pdb").write_text("END\n", encoding="utf-8")
                (candidate_dir / "ranking_debug.json").write_text("{}\n", encoding="utf-8")

            listed = dispatcher.call_tool(
                "pipeline.list_artifacts",
                {"run_id": run_id, "max_depth": 6, "limit": 300},
            )
            paths = [str(item.get("path") or "") for item in (listed.get("artifacts") or []) if isinstance(item, dict)]

            self.assertLessEqual(len(paths), 300)
            self.assertIn("workflow_studio/session.json", paths)
            self.assertIn("wt/af2/ranked_0.pdb", paths)

    def test_pipeline_save_and_get_workflow_session(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {"target_fasta": fasta, "dry_run": True, "stop_after": "msa", "run_id": "workflow_session_case"},
            )
            run_id = str(out.get("run_id") or "")
            session = {
                "session_id": "studio_session_001",
                "head_run_id": run_id,
                "nodes": ["msa", "design", "af2"],
            }
            saved = dispatcher.call_tool(
                "pipeline.save_workflow_session",
                {"run_id": run_id, "session": session},
            )
            self.assertTrue(bool(saved.get("saved")))
            self.assertEqual(str(saved.get("path") or ""), "workflow_studio/session.json")

            loaded = dispatcher.call_tool("pipeline.get_workflow_session", {"run_id": run_id})
            self.assertTrue(bool(loaded.get("found")))
            self.assertEqual((loaded.get("session") or {}).get("session_id"), "studio_session_001")

    def test_pipeline_run_rejects_running_run_id(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            paths = init_run(tmp, "busy_run")
            set_status(paths, stage="init", state="running")
            with self.assertRaisesRegex(ValueError, "already running"):
                dispatcher.call_tool(
                    "pipeline.run",
                    {"target_fasta": fasta, "dry_run": True, "run_id": "busy_run"},
                )

    def test_pipeline_preflight_without_target_returns_required_inputs(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool("pipeline.preflight", {})
            self.assertFalse(bool(out.get("ok")))
            required = out.get("required_inputs") or []
            ids = {str(item.get("id")) for item in required if isinstance(item, dict)}
            self.assertIn("target_input", ids)

    def test_pipeline_preflight_bioemu_stop_requires_bioemu_use(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "target_fasta": ">q1\nACDEFGHIK\n",
                    "stop_after": "bioemu",
                },
            )
            self.assertFalse(bool(out.get("ok")))
            errors = [str(x) for x in (out.get("errors") or [])]
            self.assertTrue(any("bioemu_use" in e for e in errors))

    def test_pipeline_preflight_rfd3_stop_requires_rfd3_inputs(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "target_fasta": ">q1\nACDEFGHIK\n",
                    "stop_after": "rfd3",
                },
            )
            self.assertFalse(bool(out.get("ok")))
            errors = [str(x) for x in (out.get("errors") or [])]
            self.assertTrue(any("stop_after='rfd3'" in e for e in errors))

    def test_pipeline_preflight_accepts_sequence_only_bioemu(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "bioemu_use": True,
                    "bioemu_sequence": "ACDEFGHIK",
                    "stop_after": "bioemu",
                },
            )
            self.assertTrue(bool(out.get("ok")))
            required = out.get("required_inputs") or []
            ids = {str(item.get("id")) for item in required if isinstance(item, dict)}
            self.assertNotIn("target_input", ids)
            self.assertNotIn("fixed_positions_extra", ids)

    def test_pipeline_preflight_soluprot_start_requires_design_outputs(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            run_id = "resume_soluprot_case"
            init_run(tmp, run_id)
            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "run_id": run_id,
                    "target_fasta": ">q1\nACDEFGHIK\n",
                    "start_from": "soluprot",
                    "stop_after": "soluprot",
                },
            )
            self.assertFalse(bool(out.get("ok")))
            errors = [str(x) for x in (out.get("errors") or [])]
            self.assertTrue(any("Design/ProteinMPNN outputs" in e for e in errors))

    def test_pipeline_preflight_af2_start_accepts_existing_soluprot_passed_sequences(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            run_id = "resume_af2_case"
            paths = init_run(tmp, run_id)
            tier_dir = paths.root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)
            (tier_dir / "designs_filtered.fasta").write_text(">seq1\nACDEFGHIK\n", encoding="utf-8")
            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "run_id": run_id,
                    "target_fasta": ">q1\nACDEFGHIK\n",
                    "start_from": "af2",
                    "stop_after": "af2",
                },
            )
            self.assertTrue(bool(out.get("ok")))

    def test_pipeline_preflight_novelty_start_requires_af2_selected_sequences(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            run_id = "resume_novelty_case"
            paths = init_run(tmp, run_id)
            tier_dir = paths.root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)
            (tier_dir / "af2_selected.fasta").write_text("", encoding="utf-8")
            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "run_id": run_id,
                    "target_fasta": ">q1\nACDEFGHIK\n",
                    "start_from": "novelty",
                    "stop_after": "novelty",
                },
            )
            self.assertFalse(bool(out.get("ok")))
            errors = [str(x) for x in (out.get("errors") or [])]
            self.assertTrue(any("AF2-selected sequences" in e for e in errors))

    def test_pipeline_run_rfd3_stop_requires_rfd3_inputs(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            with self.assertRaisesRegex(ValueError, "stop_after='rfd3' requires rfd3_use=true and RFD3 inputs"):
                dispatcher.call_tool(
                    "pipeline.run",
                    {
                        "target_fasta": ">q1\nACDEFGHIK\n",
                        "stop_after": "rfd3",
                        "dry_run": True,
                    },
                )

    def test_auto_retry_does_not_retry_cancelled_error(self) -> None:
        req = PipelineRequest(target_fasta=">q1\nACDE\n", target_pdb="", dry_run=False)

        class _StubRunner:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, request: PipelineRequest, *, run_id: str | None = None):  # type: ignore[no-untyped-def]
                self.calls += 1
                raise RuntimeError("MMseqs RunPod job not completed: {'status': 'CANCELLED'}")

        stub = _StubRunner()
        retry = AutoRetryConfig(enabled=True, max_attempts=3, backoff_s=0.0)
        with self.assertRaisesRegex(RuntimeError, "CANCELLED"):
            _run_with_auto_retry(stub, req, run_id="cancel_case", retry=retry)  # type: ignore[arg-type]
        self.assertEqual(stub.calls, 1)

    def test_pipeline_run_tool_accepts_rfd3_inputs(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None, rfd3=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {
                    "rfd3_contig": "A1-2",
                    "rfd3_input_pdb": pdb,
                    "dry_run": True,
                    "num_seq_per_tier": 1,
                    "conservation_tiers": [0.3],
                },
            )
            self.assertIn("run_id", out)
            self.assertIn("output_dir", out)

    def test_pipeline_request_parses_bioemu_args(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "bioemu_use": True,
                "bioemu_num_samples": 25,
                "bioemu_model_name": "bioemu-v1.1",
                "bioemu_filter_samples": False,
                "bioemu_max_return_structures": 12,
                "bioemu_base_seed": 7,
                "bioemu_steering_config_text": "guiding_potential:\n  type: harmonic\n",
                "af2_max_candidates_per_tier": 5,
                "bioemu_env": {"BIOEMU_COLABFOLD_DIR": "/runpod-volume/bioemu/colabfold"},
                "ligand_mask_use_original_target": False,
            }
        )
        self.assertTrue(req.bioemu_use)
        self.assertEqual(req.bioemu_num_samples, 25)
        self.assertEqual(req.bioemu_model_name, "bioemu-v1.1")
        self.assertFalse(req.bioemu_filter_samples)
        self.assertEqual(req.bioemu_max_return_structures, 12)
        self.assertEqual(req.bioemu_base_seed, 7)
        self.assertEqual(req.bioemu_steering_config_text, "guiding_potential:\n  type: harmonic")
        self.assertEqual(req.af2_max_candidates_per_tier, 5)
        self.assertEqual(req.bioemu_env, {"BIOEMU_COLABFOLD_DIR": "/runpod-volume/bioemu/colabfold"})
        self.assertFalse(req.ligand_mask_use_original_target)

    def test_pipeline_request_defaults_original_ligand_mask_on(self) -> None:
        req = pipeline_request_from_args({"target_fasta": ">q1\nACDEFGHIK\n"})
        self.assertTrue(req.ligand_mask_use_original_target)

    def test_pipeline_request_defaults_rfd3_target_rmsd_cutoff_when_omitted(self) -> None:
        req = pipeline_request_from_args({"target_fasta": ">q1\nACDEFGHIK\n"})
        self.assertEqual(req.rfd3_target_rmsd_cutoff, 2.0)

    def test_pipeline_request_preserves_explicit_zero_rfd3_target_rmsd_cutoff(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "rfd3_target_rmsd_cutoff": 0,
            }
        )
        self.assertEqual(req.rfd3_target_rmsd_cutoff, 0.0)

    def test_pipeline_request_defaults_bioemu_target_rmsd_cutoff_when_omitted(self) -> None:
        req = pipeline_request_from_args({"target_fasta": ">q1\nACDEFGHIK\n", "bioemu_use": True})
        self.assertEqual(req.bioemu_target_rmsd_cutoff, 2.0)

    def test_pipeline_request_preserves_explicit_zero_bioemu_target_rmsd_cutoff(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "bioemu_use": True,
                "bioemu_target_rmsd_cutoff": 0,
            }
        )
        self.assertEqual(req.bioemu_target_rmsd_cutoff, 0.0)

    def test_pipeline_request_defaults_backbone_filter_use_dssp_when_omitted(self) -> None:
        req = pipeline_request_from_args({"target_fasta": ">q1\nACDEFGHIK\n", "bioemu_use": True})
        self.assertTrue(req.backbone_filter_use_dssp)

    def test_pipeline_request_preserves_explicit_false_backbone_filter_use_dssp(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "bioemu_use": True,
                "backbone_filter_use_dssp": False,
            }
        )
        self.assertFalse(req.backbone_filter_use_dssp)

    def test_pipeline_request_without_target_or_rfd3_inputs_still_fails_when_cutoff_omitted(self) -> None:
        with self.assertRaisesRegex(ValueError, "One of target_fasta or target_pdb or rfd3 inputs is required"):
            pipeline_request_from_args({})

    def test_pipeline_request_parses_relax_args(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "relax_enabled": True,
                "relax_score_per_residue_cutoff": -2.5,
                "relax_nstruct": 2,
                "relax_extra_flags": "-ex1 -use_input_sc",
            }
        )
        self.assertTrue(req.relax_enabled)
        self.assertEqual(req.relax_score_per_residue_cutoff, -2.5)
        self.assertEqual(req.relax_nstruct, 2)
        self.assertEqual(req.relax_extra_flags, "-ex1 -use_input_sc")

    def test_pipeline_request_defaults_bioemu_filter_samples_on(self) -> None:
        req = pipeline_request_from_args({"target_fasta": ">q1\nACDEFGHIK\n", "bioemu_use": True})
        self.assertTrue(req.bioemu_filter_samples)

    def test_pipeline_request_defaults_bioemu_num_samples_to_oversampled_return_count(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "bioemu_use": True,
                "bioemu_max_return_structures": 10,
            }
        )
        self.assertEqual(req.bioemu_max_return_structures, 10)
        self.assertEqual(req.bioemu_num_samples, 50)
        self.assertEqual(req.bioemu_max_attempted_structures, 200)

    def test_pipeline_request_defaults_bioemu_num_samples_to_return_count_when_filter_disabled(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "bioemu_use": True,
                "bioemu_filter_samples": False,
                "bioemu_max_return_structures": 12,
            }
        )
        self.assertFalse(req.bioemu_filter_samples)
        self.assertEqual(req.bioemu_max_return_structures, 12)
        self.assertEqual(req.bioemu_num_samples, 12)

    def test_pipeline_request_parses_rfd3_mode_controls(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "rfd3_input_pdb": "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n",
                "rfd3_mode": "enzyme",
                "rfd3_unindex": "A10-20",
                "rfd3_length": "20-40",
                "rfd3_select_fixed_atoms": "A57:CA,A57:CB",
                "rfd3_partial_t": 7.5,
                "rfd3_sampling_strategy": "independent_jobs",
                "rfd3_fail_on_duplicate_backbones": True,
            }
        )
        self.assertEqual(req.rfd3_mode, "enzyme")
        self.assertEqual(req.rfd3_unindex, "A10-20")
        self.assertEqual(req.rfd3_length, "20-40")
        self.assertEqual(req.rfd3_select_fixed_atoms, "A57:CA,A57:CB")
        self.assertEqual(req.rfd3_partial_t, 7.5)
        self.assertEqual(req.rfd3_sampling_strategy, "independent_jobs")
        self.assertTrue(req.rfd3_fail_on_duplicate_backbones)

    def test_pipeline_request_parses_rfd3_select_fixed_atoms_json_object_string(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "rfd3_input_pdb": "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n",
                "rfd3_mode": "local_diversify",
                "rfd3_unindex": "A1",
                "rfd3_select_fixed_atoms": "{\"A1\":\"ALL\"}",
            }
        )
        self.assertEqual(req.rfd3_mode, "local_diversify")
        self.assertEqual(req.rfd3_unindex, "A1")
        self.assertEqual(req.rfd3_select_fixed_atoms, {"A1": "ALL"})

    def test_pipeline_request_preserves_explicit_rfd3_disable_state(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_pdb": "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n",
                "rfd3_use": False,
                "rfd3_input_pdb": "ATOM      1  CA  ALA A   1       1.000   0.000   0.000  1.00 20.00           C\nEND\n",
                "rfd3_mode": "local_diversify",
            }
        )
        self.assertFalse(req.rfd3_use)
        self.assertEqual(req.rfd3_mode, "local_diversify")

    def test_pipeline_request_inferrs_legacy_rfd3_enable_when_flag_missing(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "rfd3_input_pdb": "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n",
                "rfd3_mode": "local_diversify",
            }
        )
        self.assertIsNone(req.rfd3_use)
        self.assertEqual(req.rfd3_mode, "local_diversify")

    def test_pipeline_request_defaults_wt_diff_enabled(self) -> None:
        req = pipeline_request_from_args({"target_fasta": ">q1\nACDEFGHIK\n"})
        self.assertTrue(req.novelty_enabled)

    def test_pipeline_request_normalizes_wt_diff_stage_alias(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "start_from": "wt_diff",
                "stop_after": "wt_diff",
            }
        )
        self.assertEqual(req.start_from, "novelty")
        self.assertEqual(req.stop_after, "novelty")

    def test_pipeline_request_parses_start_from(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "start_from": "SoLuPrOt",
                "stop_after": "novelty",
            }
        )
        self.assertEqual(req.start_from, "soluprot")
        self.assertEqual(req.stop_after, "novelty")

    def test_pipeline_request_parses_selected_tiers_subset(self) -> None:
        req = pipeline_request_from_args(
            {
                "target_fasta": ">q1\nACDEFGHIK\n",
                "conservation_tiers": [0.3, 0.5, 0.7],
                "selected_tiers": [0.5],
            }
        )
        self.assertEqual(req.conservation_tiers, [0.3, 0.5, 0.7])
        self.assertEqual(req.selected_tiers, [0.5])

    def test_pipeline_preflight_rejects_start_from_after_stop_after(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "target_fasta": ">q1\nACDEFGHIK\n",
                    "start_from": "af2",
                    "stop_after": "msa",
                },
            )
            self.assertFalse(bool(out.get("ok")))
            errors = [str(x) for x in (out.get("errors") or [])]
            self.assertTrue(any("start_from" in e and "stop_after" in e for e in errors))

    def test_pipeline_run_bioemu_stop_dry_run_without_target_pdb(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None, bioemu=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {
                    "target_fasta": fasta,
                    "bioemu_use": True,
                    "stop_after": "bioemu",
                    "bioemu_num_samples": 2,
                    "bioemu_max_return_structures": 2,
                    "dry_run": True,
                },
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

    def test_pipeline_preflight_rejects_stop_after_rfd3_when_rfd3_is_disabled(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.preflight",
                {
                    "target_pdb": "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n",
                    "rfd3_use": False,
                    "rfd3_input_pdb": "ATOM      1  CA  ALA A   1       1.000   0.000   0.000  1.00 20.00           C\nEND\n",
                    "stop_after": "rfd3",
                },
            )
            self.assertFalse(bool(out.get("ok")))
            errors = [str(x) for x in (out.get("errors") or [])]
            self.assertTrue(any("rfd3_use=true" in e for e in errors))

    def test_pipeline_af2_predict_dry_run(self) -> None:
        fasta = ">s1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.af2_predict",
                {"target_fasta": fasta, "dry_run": True},
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            listing = dispatcher.call_tool("pipeline.list_artifacts", {"run_id": run_id, "limit": 200})
            artifacts = listing.get("artifacts") or []
            paths = {str(a.get("path")) for a in artifacts if isinstance(a, dict)}
            self.assertIn("af2/s1/ranked_0.pdb", paths)

    def test_pipeline_diffdock_dry_run(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.diffdock",
                {"protein_pdb": pdb, "ligand_smiles": "CCO", "dry_run": True},
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            listing = dispatcher.call_tool("pipeline.list_artifacts", {"run_id": run_id, "limit": 200})
            artifacts = listing.get("artifacts") or []
            paths = {str(a.get("path")) for a in artifacts if isinstance(a, dict)}
            self.assertIn("diffdock/output.json", paths)

    def test_pipeline_diffdock_dry_run_normalizes_modelserver_ligand_text(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        modelserver_cif = "\n".join(
            [
                "data_6CKL",
                "#",
                "_model_server_result.job_id abc",
                "loop_",
                "_chem_comp_bond.atom_id_1",
                "_chem_comp_bond.atom_id_2",
                "_chem_comp_bond.comp_id",
                "_chem_comp_bond.value_order",
                "C1 O1 LIG sing",
                "#",
                "loop_",
                "_atom_site.group_PDB",
                "_atom_site.id",
                "_atom_site.type_symbol",
                "_atom_site.label_atom_id",
                "_atom_site.label_comp_id",
                "_atom_site.label_seq_id",
                "_atom_site.label_alt_id",
                "_atom_site.pdbx_PDB_ins_code",
                "_atom_site.label_asym_id",
                "_atom_site.label_entity_id",
                "_atom_site.Cartn_x",
                "_atom_site.Cartn_y",
                "_atom_site.Cartn_z",
                "_atom_site.occupancy",
                "_atom_site.B_iso_or_equiv",
                "_atom_site.pdbx_formal_charge",
                "_atom_site.auth_atom_id",
                "_atom_site.auth_comp_id",
                "_atom_site.auth_seq_id",
                "_atom_site.auth_asym_id",
                "_atom_site.pdbx_PDB_model_num",
                "HETATM 1 C C1 LIG . . . D 1 0.000 0.000 0.000 1 20.00 ? C1 LIG 1 A 1",
                "HETATM 2 O O1 LIG . . . D 1 1.200 0.000 0.000 1 20.00 ? O1 LIG 1 A 1",
                "#",
            ]
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.diffdock",
                {"protein_pdb": pdb, "ligand_smiles": modelserver_cif, "dry_run": True},
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            listing = dispatcher.call_tool("pipeline.list_artifacts", {"run_id": run_id, "limit": 200})
            artifacts = listing.get("artifacts") or []
            paths = {str(a.get("path")) for a in artifacts if isinstance(a, dict)}
            self.assertIn("diffdock/ligand.sdf", paths)
            self.assertIn("diffdock/rank1.sdf", paths)
            self.assertIn("diffdock/ligand.pdb", paths)
            self.assertIn("diffdock/complex.pdb", paths)
            self.assertNotIn("diffdock/ligand.smiles", paths)

    def test_pipeline_feedback_and_report(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {"target_fasta": fasta, "dry_run": True, "num_seq_per_tier": 1, "conservation_tiers": [0.3]},
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            dispatcher.call_tool(
                "pipeline.submit_feedback",
                {"run_id": run_id, "rating": "good", "reasons": ["low_novelty"], "comment": "ok"},
            )
            feedback = dispatcher.call_tool("pipeline.list_feedback", {"run_id": run_id, "limit": 5})
            items = feedback.get("items") or []
            self.assertTrue(items)

            dispatcher.call_tool(
                "pipeline.submit_experiment",
                {
                    "run_id": run_id,
                    "result": "success",
                    "assay_type": "binding",
                    "candidate_id": "target:s1",
                    "sequence_id": "target:s1",
                    "metric_name": "activity",
                    "metric_value": 0.72,
                    "metric_unit": "relative",
                    "metric_direction": "maximize",
                    "replicate_id": "rep1",
                },
            )
            experiments = dispatcher.call_tool("pipeline.list_experiments", {"run_id": run_id, "limit": 5})
            exp_items = experiments.get("items") or []
            self.assertTrue(exp_items)
            self.assertEqual(exp_items[0].get("candidate_id"), "target:s1")
            self.assertEqual(exp_items[0].get("metric_name"), "activity")
            self.assertEqual(exp_items[0].get("metric_value"), 0.72)
            self.assertEqual(exp_items[0].get("metric_direction"), "maximize")

            report = dispatcher.call_tool("pipeline.generate_report", {"run_id": run_id})
            self.assertIn("report", report)
            report_text = str(report.get("report"))
            self.assertIn("Score", report_text)
            self.assertIn("WT Comparison", report_text)
            self.assertIn("Backbone Source Comparison", report_text)
            self.assertIn("Backbone generation/use", report_text)
            self.assertIn("WT change (n/len · identity)", report_text)
            comparison_summary = report.get("comparison_summary") or {}
            self.assertIn("wt_vs_design", comparison_summary)
            self.assertIn("source_compare", comparison_summary)
            self.assertIn("funnel", comparison_summary)
            self.assertIn("tier_compare", comparison_summary)
            self.assertIn("distributions", comparison_summary)
            self.assertIn("diversity", comparison_summary)
            source_compare = comparison_summary.get("source_compare") or {}
            if isinstance(source_compare, dict):
                for bucket in source_compare.values():
                    if not isinstance(bucket, dict):
                        continue
                    self.assertIn("requested_count", bucket)
                    self.assertIn("observed_count", bucket)
                    self.assertIn("materialized_count", bucket)
                    self.assertIn("propagated_count", bucket)
                    self.assertIn("propagation_mode", bucket)
                    self.assertIn("plddt_median", bucket)
                    self.assertIn("rmsd_median", bucket)
            tier_compare = comparison_summary.get("tier_compare") or []
            if isinstance(tier_compare, list):
                for row in tier_compare:
                    if not isinstance(row, dict):
                        continue
                    self.assertIn("plddt_median", row)
                    self.assertIn("rmsd_median", row)

            listing = dispatcher.call_tool("pipeline.list_artifacts", {"run_id": run_id, "limit": 200})
            artifacts = listing.get("artifacts") or []
            paths = {str(a.get("path")) for a in artifacts if isinstance(a, dict)}
            self.assertIn("comparisons.json", paths)

    def test_pipeline_artifact_tools(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {
                    "target_fasta": fasta,
                    "target_pdb": pdb,
                    "dry_run": True,
                    "num_seq_per_tier": 1,
                    "conservation_tiers": [0.3],
                },
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            listing = dispatcher.call_tool("pipeline.list_artifacts", {"run_id": run_id, "limit": 200})
            artifacts = listing.get("artifacts") or []
            paths = {str(a.get("path")) for a in artifacts if isinstance(a, dict)}
            self.assertIn("request.json", paths)

            read_out = dispatcher.call_tool(
                "pipeline.read_artifact",
                {"run_id": run_id, "path": "request.json", "max_bytes": 64},
            )
            self.assertIn("text", read_out)
            self.assertLessEqual(int(read_out.get("read_bytes") or 0), 64)

    def test_get_report_includes_comparison_summary_even_without_prebuilt_artifact(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {"target_fasta": fasta, "dry_run": True, "num_seq_per_tier": 1, "conservation_tiers": [0.3]},
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            dispatcher.call_tool("pipeline.generate_report", {"run_id": run_id})
            comp_path = Path(tmp) / run_id / "comparisons.json"
            if comp_path.exists():
                comp_path.unlink()

            report_payload = dispatcher.call_tool("pipeline.get_report", {"run_id": run_id})
            comparison_summary = report_payload.get("comparison_summary") or {}
            self.assertIn("wt_vs_design", comparison_summary)
            self.assertIn("source_compare", comparison_summary)
            self.assertIn("funnel", comparison_summary)
            self.assertIn("tier_compare", comparison_summary)
            source_compare = comparison_summary.get("source_compare") or {}
            if isinstance(source_compare, dict):
                for bucket in source_compare.values():
                    if not isinstance(bucket, dict):
                        continue
                    self.assertIn("requested_count", bucket)
                    self.assertIn("observed_count", bucket)
                    self.assertIn("materialized_count", bucket)
                    self.assertIn("propagated_count", bucket)
                    self.assertIn("propagation_mode", bucket)
                    self.assertIn("plddt_median", bucket)
                    self.assertIn("rmsd_median", bucket)

    def test_recovered_af2_placeholders_are_ignored_in_comparison_summary_and_hit_list(self) -> None:
        with _tmpdir() as tmp:
            run_root = Path(tmp)
            tier_dir = run_root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)

            (tier_dir / "soluprot.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "rfd3_model:1": 0.9,
                            "bioemu_model:1": 0.8,
                        },
                        "passed_ids": ["rfd3_model:1", "bioemu_model:1"],
                    }
                ),
                encoding="utf-8",
            )
            (tier_dir / "af2_scores.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "rfd3_model:1": 0.0,
                            "bioemu_model:1": 0.0,
                        },
                        "rmsd_scores": {},
                        "candidate_ids": ["rfd3_model:1", "bioemu_model:1"],
                        "selected_ids": ["rfd3_model:1", "bioemu_model:1"],
                        "recovered": True,
                        "error": "af2_30 failed: no PDB outputs were found",
                    }
                ),
                encoding="utf-8",
            )

            summary = {
                "tiers": [
                    {
                        "tier": 0.3,
                        "proteinmpnn_samples": [
                            {
                                "id": "rfd3_model:1",
                                "sequence": "ACDE",
                                "meta": {"backbone_source": "rfd3"},
                            },
                            {
                                "id": "bioemu_model:1",
                                "sequence": "ACDF",
                                "meta": {"backbone_source": "bioemu"},
                            },
                        ],
                    }
                ]
            }
            request = {"target_fasta": ">q1\nACDE\n", "wt_compare": True}

            comparison_summary = _build_comparison_summary(run_root=run_root, request=request, summary=summary)
            tier_compare = comparison_summary.get("tier_compare") or []
            self.assertEqual(len(tier_compare), 1)
            row = tier_compare[0]
            self.assertEqual(int(row.get("af2_candidate_total") or 0), 2)
            self.assertEqual(int(row.get("af2_selected_total") or 0), 0)
            self.assertIsNone(row.get("plddt_median"))
            self.assertIsNone(row.get("rmsd_median"))

            source_compare = comparison_summary.get("source_compare") or {}
            self.assertEqual(int((source_compare.get("rfd3") or {}).get("af2_candidate_total") or 0), 1)
            self.assertEqual(int((source_compare.get("rfd3") or {}).get("af2_selected_total") or 0), 0)
            self.assertIsNone((source_compare.get("rfd3") or {}).get("plddt_median"))
            self.assertIsNone((source_compare.get("rfd3") or {}).get("rmsd_median"))

            hit_rows = _build_hit_list_rows(
                run_root=run_root,
                request=request,
                summary=summary,
                weights={"soluprot": 1.0, "plddt": 1.0, "rmsd": 1.0, "novelty": 0.0},
                rmsd_ref=5.0,
            )
            self.assertEqual(len(hit_rows), 2)
            for row in hit_rows:
                self.assertIsNone(row.get("plddt"))
                self.assertIsNone(row.get("rmsd"))
                self.assertTrue(bool(row.get("af2_candidate")))
                self.assertFalse(bool(row.get("af2_selected")))

    def test_comparison_summary_and_hit_list_include_relax_metrics(self) -> None:
        with _tmpdir() as tmp:
            run_root = Path(tmp)
            tier_dir = run_root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)
            wt_dir = run_root / "wt"
            wt_dir.mkdir(parents=True, exist_ok=True)

            (tier_dir / "soluprot.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "rfd3_model:1": 0.91,
                            "bioemu_model:1": 0.83,
                        },
                        "passed_ids": ["rfd3_model:1", "bioemu_model:1"],
                    }
                ),
                encoding="utf-8",
            )
            (tier_dir / "af2_scores.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "rfd3_model:1": 92.0,
                            "bioemu_model:1": 88.0,
                        },
                        "rmsd_scores": {
                            "rfd3_model:1": 0.8,
                            "bioemu_model:1": 1.4,
                        },
                        "candidate_ids": ["rfd3_model:1", "bioemu_model:1"],
                        "selected_ids": ["rfd3_model:1", "bioemu_model:1"],
                    }
                ),
                encoding="utf-8",
            )
            (tier_dir / "relax_scores.json").write_text(
                json.dumps(
                    {
                        "score_per_residue": {
                            "rfd3_model:1": -3.5,
                            "bioemu_model:1": -2.1,
                        },
                        "total_scores": {
                            "rfd3_model:1": -350.0,
                            "bioemu_model:1": -210.0,
                        },
                        "delta_total_scores": {
                            "rfd3_model:1": -140.0,
                            "bioemu_model:1": -90.0,
                        },
                        "candidate_ids": ["rfd3_model:1", "bioemu_model:1"],
                        "selected_ids": ["rfd3_model:1"],
                        "cutoff": -3.0,
                    }
                ),
                encoding="utf-8",
            )
            (wt_dir / "metrics.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "relax": {
                            "score_per_residue": -3.0,
                            "total_score": -300.0,
                            "delta_total_score": -120.0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = {
                "tiers": [
                    {
                        "tier": 0.3,
                        "proteinmpnn_samples": [
                            {
                                "id": "rfd3_model:1",
                                "sequence": "ACDE",
                                "meta": {"backbone_source": "rfd3"},
                            },
                            {
                                "id": "bioemu_model:1",
                                "sequence": "ACDF",
                                "meta": {"backbone_source": "bioemu"},
                            },
                        ],
                    }
                ]
            }
            request = {"target_fasta": ">q1\nACDE\n", "wt_compare": True, "relax_enabled": True}

            comparison_summary = _build_comparison_summary(run_root=run_root, request=request, summary=summary)
            relax_metric = (comparison_summary.get("wt_vs_design") or {}).get("relax") or {}
            self.assertAlmostEqual(float(relax_metric.get("wt") or 0.0), -3.0, places=6)
            self.assertAlmostEqual(float(relax_metric.get("design_median") or 0.0), -2.8, places=6)
            self.assertAlmostEqual(float(relax_metric.get("delta_design_minus_wt") or 0.0), 0.2, places=6)

            source_compare = comparison_summary.get("source_compare") or {}
            self.assertAlmostEqual(float((source_compare.get("rfd3") or {}).get("relax_median") or 0.0), -3.5, places=6)
            self.assertAlmostEqual(float((source_compare.get("bioemu") or {}).get("relax_median") or 0.0), -2.1, places=6)

            tier_compare = comparison_summary.get("tier_compare") or []
            self.assertEqual(len(tier_compare), 1)
            self.assertAlmostEqual(float((tier_compare[0] or {}).get("relax_median") or 0.0), -2.8, places=6)
            self.assertEqual(int((tier_compare[0] or {}).get("relax_selected_total") or 0), 1)

            relax_distribution = (comparison_summary.get("distributions") or {}).get("relax") or {}
            self.assertEqual(int(relax_distribution.get("count") or 0), 2)
            self.assertAlmostEqual(float(relax_distribution.get("median") or 0.0), -2.8, places=6)

            hit_rows = _build_hit_list_rows(
                run_root=run_root,
                request=request,
                summary=summary,
                weights={"soluprot": 1.0, "plddt": 1.0, "rmsd": 1.0, "novelty": 0.0},
                rmsd_ref=5.0,
            )
            self.assertEqual(len(hit_rows), 2)
            by_id = {str(row.get("seq_id")): row for row in hit_rows}
            self.assertAlmostEqual(float((by_id.get("rfd3_model:1") or {}).get("relax") or 0.0), -3.5, places=6)
            self.assertAlmostEqual(float((by_id.get("bioemu_model:1") or {}).get("relax") or 0.0), -2.1, places=6)
            self.assertTrue(bool((by_id.get("rfd3_model:1") or {}).get("relax_selected")))
            self.assertFalse(bool((by_id.get("bioemu_model:1") or {}).get("relax_selected")))

    def test_wt_comparison_uses_target_rmsd_when_available_but_hit_list_keeps_parent_backbone_rmsd(self) -> None:
        with _tmpdir() as tmp:
            run_root = Path(tmp)
            tier_dir = run_root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)
            wt_dir = run_root / "wt"
            wt_dir.mkdir(parents=True, exist_ok=True)

            (tier_dir / "soluprot.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "rfd3_model:1": 0.91,
                            "bioemu_model:1": 0.83,
                        },
                        "passed_ids": ["rfd3_model:1", "bioemu_model:1"],
                    }
                ),
                encoding="utf-8",
            )
            (tier_dir / "af2_scores.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "rfd3_model:1": 92.0,
                            "bioemu_model:1": 88.0,
                        },
                        "rmsd_scores": {
                            "rfd3_model:1": 0.4,
                            "bioemu_model:1": 0.2,
                        },
                        "target_rmsd_scores": {
                            "rfd3_model:1": 0.4,
                            "bioemu_model:1": 2.4,
                        },
                        "candidate_ids": ["rfd3_model:1", "bioemu_model:1"],
                        "selected_ids": ["rfd3_model:1", "bioemu_model:1"],
                    }
                ),
                encoding="utf-8",
            )
            (wt_dir / "metrics.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "af2": {
                            "best_plddt": 90.0,
                            "rmsd_ca": 0.5,
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = {
                "tiers": [
                    {
                        "tier": 0.3,
                        "proteinmpnn_samples": [
                            {
                                "id": "rfd3_model:1",
                                "sequence": "ACDE",
                                "meta": {"backbone_source": "rfd3"},
                            },
                            {
                                "id": "bioemu_model:1",
                                "sequence": "ACDF",
                                "meta": {"backbone_source": "bioemu"},
                            },
                        ],
                    }
                ]
            }
            request = {"target_fasta": ">q1\nACDE\n", "wt_compare": True}

            comparison_summary = _build_comparison_summary(run_root=run_root, request=request, summary=summary)
            rmsd_metric = (comparison_summary.get("wt_vs_design") or {}).get("rmsd") or {}
            self.assertAlmostEqual(float(rmsd_metric.get("wt") or 0.0), 0.5, places=6)
            self.assertAlmostEqual(float(rmsd_metric.get("design_median") or 0.0), 1.4, places=6)
            self.assertAlmostEqual(float(rmsd_metric.get("delta_design_minus_wt") or 0.0), 0.9, places=6)

            source_compare = comparison_summary.get("source_compare") or {}
            self.assertAlmostEqual(float((source_compare.get("rfd3") or {}).get("rmsd_median") or 0.0), 0.4, places=6)
            self.assertAlmostEqual(float((source_compare.get("bioemu") or {}).get("rmsd_median") or 0.0), 0.2, places=6)

            hit_rows = _build_hit_list_rows(
                run_root=run_root,
                request=request,
                summary=summary,
                weights={"soluprot": 1.0, "plddt": 1.0, "rmsd": 1.0, "novelty": 0.0},
                rmsd_ref=5.0,
            )
            by_id = {str(row.get("seq_id")): row for row in hit_rows}
            self.assertAlmostEqual(float((by_id.get("rfd3_model:1") or {}).get("rmsd") or 0.0), 0.4, places=6)
            self.assertAlmostEqual(float((by_id.get("bioemu_model:1") or {}).get("rmsd") or 0.0), 0.2, places=6)

    def test_hit_list_prefers_parent_backbone_metrics_rmsd_when_available(self) -> None:
        with _tmpdir() as tmp:
            run_root = Path(tmp)
            tier_dir = run_root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)
            af2_dir = tier_dir / "af2"
            (af2_dir / "rfd3_model_1").mkdir(parents=True, exist_ok=True)
            (af2_dir / "bioemu_model_1").mkdir(parents=True, exist_ok=True)

            (tier_dir / "soluprot.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "rfd3_model:1": 0.91,
                            "bioemu_model:1": 0.83,
                        },
                        "passed_ids": ["rfd3_model:1", "bioemu_model:1"],
                    }
                ),
                encoding="utf-8",
            )
            (tier_dir / "af2_scores.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "rfd3_model:1": 92.0,
                            "bioemu_model:1": 88.0,
                        },
                        "rmsd_scores": {
                            "rfd3_model:1": 4.8,
                            "bioemu_model:1": 2.2,
                        },
                        "target_rmsd_scores": {
                            "rfd3_model:1": 1.3,
                            "bioemu_model:1": 1.7,
                        },
                        "candidate_ids": ["rfd3_model:1", "bioemu_model:1"],
                        "selected_ids": ["rfd3_model:1"],
                    }
                ),
                encoding="utf-8",
            )
            (af2_dir / "rfd3_model_1" / "metrics.json").write_text(
                json.dumps(
                    {
                        "rmsd_ca": 1.1,
                        "rmsd_reference_mode": "parent_backbone",
                    }
                ),
                encoding="utf-8",
            )
            (af2_dir / "bioemu_model_1" / "metrics.json").write_text(
                json.dumps(
                    {
                        "rmsd_ca": 0.4,
                        "rmsd_reference_mode": "target_reference",
                    }
                ),
                encoding="utf-8",
            )

            summary = {
                "tiers": [
                    {
                        "tier": 0.3,
                        "proteinmpnn_samples": [
                            {
                                "id": "rfd3_model:1",
                                "sequence": "ACDE",
                                "meta": {"backbone_source": "rfd3"},
                            },
                            {
                                "id": "bioemu_model:1",
                                "sequence": "ACDF",
                                "meta": {"backbone_source": "bioemu"},
                            },
                        ],
                    }
                ]
            }
            request = {"target_fasta": ">q1\nACDE\n", "wt_compare": True}

            hit_rows = _build_hit_list_rows(
                run_root=run_root,
                request=request,
                summary=summary,
                weights={"soluprot": 1.0, "plddt": 1.0, "rmsd": 1.0, "novelty": 0.0},
                rmsd_ref=5.0,
            )
            by_id = {str(row.get("seq_id")): row for row in hit_rows}
            self.assertAlmostEqual(
                float((by_id.get("rfd3_model:1") or {}).get("rmsd") or 0.0), 1.1, places=6
            )
            self.assertAlmostEqual(
                float((by_id.get("bioemu_model:1") or {}).get("rmsd") or 0.0), 2.2, places=6
            )
            self.assertAlmostEqual(
                float((by_id.get("rfd3_model:1") or {}).get("rmsd_target") or 0.0), 1.3, places=6
            )

    def test_comparison_summary_hides_target_designs_when_generated_sources_exist(self) -> None:
        with _tmpdir() as tmp:
            run_root = Path(tmp)
            tier_dir = run_root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)

            (tier_dir / "soluprot.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "target:1": 0.51,
                            "bioemu_model:1": 0.83,
                        },
                        "passed_ids": ["target:1", "bioemu_model:1"],
                    }
                ),
                encoding="utf-8",
            )

            summary = {
                "tiers": [
                    {
                        "tier": 0.3,
                        "proteinmpnn_samples": [
                            {
                                "id": "target:1",
                                "sequence": "ACDE",
                                "meta": {"backbone_source": "target"},
                            },
                            {
                                "id": "bioemu_model:1",
                                "sequence": "ACDF",
                                "meta": {"backbone_source": "bioemu"},
                            },
                        ],
                    }
                ]
            }
            request = {"target_fasta": ">q1\nACDE\n", "wt_compare": True}

            comparison_summary = _build_comparison_summary(run_root=run_root, request=request, summary=summary)
            tier_compare = comparison_summary.get("tier_compare") or []
            self.assertEqual(len(tier_compare), 1)
            row = tier_compare[0]
            self.assertEqual(int(row.get("design_total") or 0), 1)
            self.assertEqual(int((row.get("source_counts") or {}).get("bioemu") or 0), 1)
            self.assertEqual(int((row.get("source_counts") or {}).get("other") or 0), 0)

            source_compare = comparison_summary.get("source_compare") or {}
            self.assertEqual(int((source_compare.get("bioemu") or {}).get("soluprot_total") or 0), 1)
            self.assertEqual(int((source_compare.get("other") or {}).get("soluprot_total") or 0), 0)

            diversity = comparison_summary.get("diversity") or {}
            self.assertEqual(int((diversity.get("design_unique_sequences") or 0)), 1)

            hit_rows = _build_hit_list_rows(
                run_root=run_root,
                request=request,
                summary=summary,
                weights={"soluprot": 1.0, "plddt": 1.0, "rmsd": 1.0, "novelty": 0.0},
                rmsd_ref=5.0,
            )
            self.assertEqual(len(hit_rows), 1)
            self.assertEqual(str((hit_rows[0] or {}).get("seq_id") or ""), "bioemu_model:1")
            self.assertEqual(str((hit_rows[0] or {}).get("source") or ""), "bioemu")

    def test_comparison_summary_keeps_target_designs_for_target_only_runs(self) -> None:
        with _tmpdir() as tmp:
            run_root = Path(tmp)
            tier_dir = run_root / "tiers" / "30"
            tier_dir.mkdir(parents=True, exist_ok=True)

            (tier_dir / "soluprot.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "target:1": 0.51,
                        },
                        "passed_ids": ["target:1"],
                    }
                ),
                encoding="utf-8",
            )

            summary = {
                "tiers": [
                    {
                        "tier": 0.3,
                        "proteinmpnn_samples": [
                            {
                                "id": "target:1",
                                "sequence": "ACDE",
                                "meta": {"backbone_source": "target"},
                            }
                        ],
                    }
                ]
            }
            request = {"target_fasta": ">q1\nACDE\n", "wt_compare": True}

            comparison_summary = _build_comparison_summary(run_root=run_root, request=request, summary=summary)
            tier_compare = comparison_summary.get("tier_compare") or []
            self.assertEqual(len(tier_compare), 1)
            row = tier_compare[0]
            self.assertEqual(int(row.get("design_total") or 0), 1)
            self.assertEqual(int((row.get("source_counts") or {}).get("other") or 0), 1)

            source_compare = comparison_summary.get("source_compare") or {}
            self.assertEqual(int((source_compare.get("other") or {}).get("soluprot_total") or 0), 1)

            hit_rows = _build_hit_list_rows(
                run_root=run_root,
                request=request,
                summary=summary,
                weights={"soluprot": 1.0, "plddt": 1.0, "rmsd": 1.0, "novelty": 0.0},
                rmsd_ref=5.0,
            )
            self.assertEqual(len(hit_rows), 1)
            self.assertEqual(str((hit_rows[0] or {}).get("seq_id") or ""), "target:1")
            self.assertEqual(str((hit_rows[0] or {}).get("source") or ""), "other")

    def test_compare_runs_hit_list_and_export_package_tools(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out1 = dispatcher.call_tool(
                "pipeline.run",
                {"target_fasta": fasta, "dry_run": True, "num_seq_per_tier": 1, "conservation_tiers": [0.3]},
            )
            run1 = str(out1.get("run_id") or "")
            self.assertTrue(run1)
            out2 = dispatcher.call_tool(
                "pipeline.run",
                {"target_fasta": fasta, "dry_run": True, "num_seq_per_tier": 1, "conservation_tiers": [0.3]},
            )
            run2 = str(out2.get("run_id") or "")
            self.assertTrue(run2)

            compare = dispatcher.call_tool(
                "pipeline.compare_runs",
                {"run_id": run2, "baseline_run_id": run1},
            )
            self.assertEqual(compare.get("run_id"), run2)
            self.assertEqual(compare.get("baseline_run_id"), run1)
            self.assertIn("delta", compare)

            hit_list = dispatcher.call_tool(
                "pipeline.get_hit_list",
                {"run_id": run2, "limit": 50, "min_score": 0.0},
            )
            self.assertEqual(hit_list.get("run_id"), run2)
            self.assertIn("rows", hit_list)
            self.assertIsInstance(hit_list.get("rows"), list)

            dispatcher.call_tool("pipeline.generate_report", {"run_id": run2})
            package = dispatcher.call_tool(
                "pipeline.export_results_package",
                {"run_id": run2, "include_top_n": 5},
            )
            path = str(package.get("path") or "")
            self.assertTrue(path.endswith(".zip"))
            self.assertTrue(path.startswith("exports/"))

            listing = dispatcher.call_tool("pipeline.list_artifacts", {"run_id": run2, "limit": 500})
            artifacts = listing.get("artifacts") or []
            paths = {str(a.get("path")) for a in artifacts if isinstance(a, dict)}
            self.assertIn(path, paths)


    def test_get_hit_list_defaults_to_surrogate_evaluated_candidates(self) -> None:
        with _tmpdir() as tmp:
            run_id = "surrogate_hit_list"
            run_root = Path(tmp) / run_id
            tier_dir = run_root / "tiers" / "30"
            triage_dir = run_root / "surrogate_triage"
            (tier_dir / "af2" / "target_sample_1").mkdir(parents=True, exist_ok=True)
            (tier_dir / "af2" / "target_sample_2").mkdir(parents=True, exist_ok=True)
            triage_dir.mkdir(parents=True, exist_ok=True)

            (run_root / "request.json").write_text(
                json.dumps(
                    {
                        "target_fasta": ">wt\nACDE\n",
                        "surrogate_triage_enabled": True,
                        "surrogate_triage_model": "auto",
                    }
                ),
                encoding="utf-8",
            )
            samples = [
                {"id": "target:sample_1", "sequence": "ACDF", "meta": {"backbone_source": "target"}},
                {"id": "target:sample_2", "sequence": "ACDG", "meta": {"backbone_source": "target"}},
                {"id": "target:sample_3", "sequence": "ACDH", "meta": {"backbone_source": "target"}},
            ]
            (run_root / "summary.json").write_text(
                json.dumps({"tiers": [{"tier": 0.3, "proteinmpnn_samples": samples}]}),
                encoding="utf-8",
            )
            (tier_dir / "soluprot.json").write_text(
                json.dumps(
                    {
                        "scores": {
                            "target:sample_1": 0.71,
                            "target:sample_2": 0.81,
                            "target:sample_3": 0.91,
                        },
                        "passed_ids": ["target:sample_1", "target:sample_2", "target:sample_3"],
                    }
                ),
                encoding="utf-8",
            )
            (tier_dir / "af2_scores.json").write_text(
                json.dumps(
                    {
                        "scores": {"target:sample_1": 88.0, "target:sample_2": 91.0},
                        "candidate_ids": ["target:sample_1", "target:sample_2"],
                        "selected_ids": ["target:sample_1", "target:sample_2"],
                    }
                ),
                encoding="utf-8",
            )
            for sample_id in ["target_sample_1", "target_sample_2"]:
                (tier_dir / "af2" / sample_id / "ranked_0.pdb").write_text(
                    "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n",
                    encoding="utf-8",
                )

            (triage_dir / "model_selection.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "model": "auto",
                        "models": ["rf", "ridge"],
                        "comparator_models": ["rf", "ridge"],
                        "selection_strategy": "auto_cv",
                        "initial_samples": 1,
                        "top_k": 1,
                        "training_ids": ["tier30_00001_target_sample_1"],
                        "selected_top_ids": ["tier30_00002_target_sample_2"],
                        "evaluated_ids": [
                            "tier30_00001_target_sample_1",
                            "tier30_00002_target_sample_2",
                        ],
                        "selected_policy": "ridge",
                        "fitted_models": ["rf", "ridge"],
                        "candidate_count_before_triage": 3,
                        "candidate_count_after_triage": 2,
                        "expected_af2_calls": 2,
                    }
                ),
                encoding="utf-8",
            )
            (triage_dir / "cv_metrics.csv").write_text(
                "policy,status,selection_score,spearman,mae,n_labels,cv_folds,error\n"
                "rf,fitted,0.2,0.2,0.5,2,2,\n"
                "ridge,fitted,0.8,0.8,0.1,2,2,\n",
                encoding="utf-8",
            )
            (triage_dir / "model_predictions.csv").write_text(
                "global_seq_id,tier,seq_id,split,af2_label,acquired,sequence,prediction_rf,rank_rf,prediction_ridge,rank_ridge\n"
                "tier30_00001_target_sample_1,30,target:sample_1,training,88.0,no,ACDF,88.1,2,88.0,2\n"
                "tier30_00002_target_sample_2,30,target:sample_2,unlabelled_pool,91.0,yes,ACDG,89.0,1,91.2,1\n"
                "tier30_00003_target_sample_3,30,target:sample_3,unlabelled_pool,,no,ACDH,88.5,3,88.6,3\n",
                encoding="utf-8",
            )
            (triage_dir / "acquired_topk.csv").write_text(
                "rank,global_seq_id,tier,seq_id,acquisition_policy,acquisition_score,af2_label,sequence\n"
                "1,tier30_00002_target_sample_2,30,target:sample_2,ridge,91.2,91.0,ACDG\n",
                encoding="utf-8",
            )

            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            hit_list = dispatcher.call_tool(
                "pipeline.get_hit_list",
                {"run_id": run_id, "limit": 50, "min_score": 0.0},
            )

            triage = hit_list.get("surrogate_triage") or {}
            self.assertEqual(triage.get("selected_policy"), "ridge")
            self.assertEqual(triage.get("selected_top_count"), 1)
            self.assertEqual(len(triage.get("cv_metrics") or []), 2)
            top_rows = triage.get("top_rows") or []
            self.assertEqual(top_rows[0].get("seq_id"), "target:sample_2")
            self.assertEqual(top_rows[0].get("acquisition_policy"), "ridge")

            rows = hit_list.get("rows") or []
            self.assertEqual(len(rows), 2)
            roles = {row.get("seq_id"): row.get("surrogate_role") for row in rows}
            self.assertEqual(roles.get("target:sample_1"), "training")
            self.assertEqual(roles.get("target:sample_2"), "top_k")
            top = next(row for row in rows if row.get("seq_id") == "target:sample_2")
            self.assertEqual(top.get("source"), "surrogate")
            self.assertEqual(top.get("backbone_source"), "other")
            self.assertEqual(top.get("surrogate_rank"), 1)
            self.assertEqual(top.get("surrogate_selected_model"), "ridge")


    def test_get_hit_list_reads_evolution_evaluated_samples(self) -> None:
        with _tmpdir() as tmp:
            run_id = "evolution_hit_list"
            run_root = Path(tmp) / run_id
            designs_dir = run_root / "evolution" / "designs"
            designs_dir.mkdir(parents=True, exist_ok=True)
            (run_root / "summary.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "evolution_mode": "multi-round-local-active-learning-kmeans",
                        "surrogate_model": "rf",
                        "budget_model": {
                            "with_surrogate": 2,
                            "without_surrogate": 100,
                            "actual_evaluated": 2,
                        },
                        "evaluated_samples": [
                            {
                                "id": "rfd3_spec-1_0_model_4:82",
                                "round": 1,
                                "phase": "round_1_top_k",
                                "plddt": 90.0,
                                "soluprot": 0.8,
                                "relax_score": -3.1,
                                "predicted_plddt": 89.5,
                                "selection_rank": 1,
                            },
                            {
                                "id": "rfd3_spec-1_0_model_1:37",
                                "round": 1,
                                "phase": "round_1_train",
                                "plddt": 88.0,
                                "soluprot": 0.7,
                                "relax_score": -2.9,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (designs_dir / "rfd3_spec-1_0_model_4:82_relaxed.pdb").write_text(
                "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n",
                encoding="utf-8",
            )
            (designs_dir / "rfd3_spec-1_0_model_1:37.pdb").write_text(
                "ATOM      1  CA  ALA A   1       1.000   0.000   0.000  1.00 20.00           C\n",
                encoding="utf-8",
            )

            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            hit_list = dispatcher.call_tool(
                "pipeline.get_hit_list",
                {"run_id": run_id, "limit": 50, "min_score": 0.0},
            )

            rows = hit_list.get("rows") or []
            self.assertEqual(hit_list.get("total_rows"), 2)
            self.assertEqual(len(rows), 2)
            self.assertIsNone(hit_list.get("surrogate_triage"))
            self.assertEqual(rows[0].get("seq_id"), "rfd3_spec-1_0_model_4:82")
            self.assertEqual(rows[0].get("source"), "evolution")
            self.assertEqual(rows[0].get("evolution_phase"), "round_1_top_k")
            self.assertEqual(rows[0].get("evolution_selection_rank"), 1)
            self.assertEqual(
                rows[0].get("af2_ranked_pdb_path"),
                "evolution/designs/rfd3_spec-1_0_model_4:82_relaxed.pdb",
            )
            self.assertEqual(
                rows[1].get("af2_ranked_pdb_path"),
                "evolution/designs/rfd3_spec-1_0_model_1:37.pdb",
            )


    def test_get_hit_list_uses_target_pdb_for_wt_difference_metrics(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {"target_pdb": pdb, "dry_run": True, "num_seq_per_tier": 1, "conservation_tiers": [0.3]},
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            hit_list = dispatcher.call_tool(
                "pipeline.get_hit_list",
                {"run_id": run_id, "limit": 50, "min_score": 0.0},
            )
            rows = hit_list.get("rows") or []
            self.assertTrue(rows)
            top = rows[0] if isinstance(rows[0], dict) else {}
            self.assertIn("wt_diff_count", top)
            self.assertIn("wt_compare_len", top)
            self.assertIn("wt_diff_pct", top)
            self.assertIsInstance(top.get("wt_compare_len"), (int, float))
            self.assertGreater(float(top.get("wt_compare_len") or 0), 0.0)
            self.assertIsInstance(top.get("novelty"), (int, float))
            self.assertIsInstance(top.get("wt_diff_ratio"), (int, float))
            self.assertAlmostEqual(
                float(top.get("novelty") or 0.0),
                float(top.get("wt_diff_ratio") or 0.0),
                places=6,
            )

    def test_get_hit_list_exposes_relax_requested_even_before_relax_metrics_exist(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {
                    "target_pdb": pdb,
                    "dry_run": True,
                    "stop_after": "soluprot",
                    "num_seq_per_tier": 1,
                    "conservation_tiers": [0.3],
                    "relax_enabled": True,
                    "relax_score_per_residue_cutoff": None,
                },
            )
            run_id = str(out.get("run_id") or "")
            self.assertTrue(run_id)

            hit_list = dispatcher.call_tool(
                "pipeline.get_hit_list",
                {"run_id": run_id, "limit": 20, "min_score": 0.0},
            )
            self.assertEqual(hit_list.get("relax_enabled"), True)

    def test_get_hit_list_prefers_saved_design_chains_for_wt_difference_metrics(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  ALA A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ALA A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  ALA A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  CYS B   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  CYS B   2       1.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  CYS B   3       2.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  CYS B   4       3.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  CYS B   5       4.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM     10  CA  CYS B   6       5.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.run",
                {"target_pdb": pdb, "dry_run": True, "num_seq_per_tier": 1, "conservation_tiers": [0.3]},
            )
            run_id = str(out.get("run_id") or "")
            run_dir = Path(str(out.get("output_dir") or ""))
            self.assertTrue(run_id)
            self.assertTrue(run_dir.exists())

            saved_chain_payload = {
                "design_chains_used": ["A"],
                "requested_design_chains": None,
                "available_chains": ["A", "B"],
            }
            (run_dir / "query_pdb_alignment.json").write_text(
                json.dumps(saved_chain_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (run_dir / "chain_strategy.json").write_text(
                json.dumps(saved_chain_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            hit_list = dispatcher.call_tool(
                "pipeline.get_hit_list",
                {"run_id": run_id, "limit": 50, "min_score": 0.0},
            )
            rows = hit_list.get("rows") or []
            self.assertTrue(rows)
            top = rows[0] if isinstance(rows[0], dict) else {}
            self.assertEqual(int(top.get("wt_compare_len") or 0), 6)
            self.assertEqual(int(top.get("wt_diff_count") or 0), 6)
            self.assertAlmostEqual(float(top.get("wt_diff_pct") or 0.0), 100.0, places=6)

    def test_pipeline_plan_from_prompt_missing_target(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.plan_from_prompt",
                {"prompt": "run design with rfd3 diffusion"},
            )
            missing = out.get("missing") or []
            self.assertIn("target_input", missing)

    def test_pipeline_plan_from_prompt_parses_contig(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.plan_from_prompt",
                {
                    "prompt": "rfd3 contig A1-2 design",
                    "target_pdb": pdb,
                },
            )
            routed = out.get("routed_request") or {}
            self.assertEqual(routed.get("rfd3_contig"), "A1-2")

    def test_pipeline_plan_from_prompt_enables_bioemu(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.plan_from_prompt",
                {"prompt": "run bioemu backbone sampling"},
            )
            routed = out.get("routed_request") or {}
            self.assertTrue(bool(routed.get("bioemu_use")))

    def test_pipeline_plan_from_prompt_defaults_wt_diff_and_num_seq_questions(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None)
            dispatcher = ToolDispatcher(runner)
            out = dispatcher.call_tool(
                "pipeline.plan_from_prompt",
                {"prompt": "run full pipeline"},
            )
            questions = out.get("questions") or []
            by_id = {
                str(item.get("id")): item
                for item in questions
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            }
            self.assertEqual((by_id.get("stop_after") or {}).get("default"), "novelty")
            self.assertEqual((by_id.get("af2_max_candidates_per_tier") or {}).get("default"), 0)
            self.assertEqual((by_id.get("num_seq_per_tier") or {}).get("default"), 2)
if __name__ == "__main__":
    unittest.main()
