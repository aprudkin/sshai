#!/usr/bin/env python3
"""Exactly one prospective delegation of original measured slots 5..36."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import benchmark_issue10_local_pilot as p
import benchmark_issue10_local_series as s
import test_issue10_local_pilot as f
from test_issue10_local_series import SeriesFixture
import test_issue10_local_series_recovery as recovery_tests
from test_issue10_intercepted_patch import streams

COMMIT = "7b201d197bb7eae320eb9068e6cf628cc008977e"


class ContinuationTests(SeriesFixture):
    prefix = recovery_tests.RecoveryTests.prefix
    recover = recovery_tests.RecoveryTests.recover

    def retained(self):
        import benchmark_issue10_patch_continuation as continuation
        self.cont = continuation
        config = json.loads(self.config.read_bytes())
        config["codex_exec"]["allowed_audit_signatures"].append({"source":"rollout.response_item","record_type":"function_call","tool_name":"exec_command","evidence_kind":"reported_call"})
        f.write(self.config,p._pretty(config))
        self.prefix()
        self.original = self.root
        self.root, manifest = self.recover()
        self.ready()
        approval = self.approve(manifest)
        self.snapshot7 = self.base / "7b-sources"
        for name, expected in continuation.PREDECESSOR_SOURCES.items():
            data = subprocess.check_output(["git", "show", COMMIT + ":" + name], cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(f.sha(data), expected)
            f.write(self.snapshot7 / name, data)
        manifest["sources"] = dict(continuation.PREDECESSOR_SOURCES)
        manifest["digest"] = p._digest_object(manifest)
        f.write(self.root / "manifest.json", p._pretty(manifest))
        ready = json.loads((self.root / "readiness/result.json").read_bytes())
        ready["manifest_digest"] = manifest["digest"]; ready["digest"] = p._digest_object(ready)
        f.write(self.root / "readiness/result.json", p._pretty(ready))
        approval = self.approve(manifest)
        approved = {"path": approval, "value": json.loads(approval.read_bytes()), "sha256": f.sha(approval.read_bytes())}
        slot = manifest["slots"][3]; base = self.root / "slots/004"; base.mkdir()
        f.write(base / "reservation.json", p._pretty({"manifest_digest":manifest["digest"], "slot":slot, "approval_sha256":approved["sha256"], "one_shot":True}))
        def collect(attempt, argv, **kwargs):
            cli, native = streams(kwargs["cwd"], kind=getattr(self, "patch_kind", "update"))
            if getattr(self,"inject_unknown",False):
                cli.insert(-1,{"type":"future.event"})
            for name, data in (("cli-source", f.jsonl(cli)), ("native-source", f.jsonl(native))):
                f.write(kwargs["cwd"] / name, data)
            code = ('import os,pathlib,sys;p=pathlib.Path;h=p(os.environ["CODEX_HOME"]);'
                    '(h/"sessions").mkdir();(h/"sessions/synthetic.jsonl").write_bytes(p("native-source").read_bytes());'
                    'p(sys.argv[1]).write_text(sys.argv[2]);sys.stdout.buffer.write(p("cli-source").read_bytes())')
            import sys
            result = p._collect_local_attempt(attempt, [sys.executable,"-c",code,str(kwargs["answer_path"]),f.ANSWER], **kwargs)
            request = result["attempt"];request["argv_sha256"] = kwargs["association"]["argv_sha256"]
            f.write(attempt / "attempt.json", p.legacy._canon(request))
            return result
        self.patch_collector = collect
        with patch.object(p,"EXPECTED_CODEX_PATH",self.codex):
            result = p.collect_reserved_slot(self.root,manifest,slot,approved,qualifier=self.fake_access,attempt_collector=collect)
        self.assertEqual(result["continuation"], {"allowed":False,"blockers":["unsupported_or_unallowed_tool_record"]})
        self.receipt = self.private / "qualification.json"
        names = {"events":"evidence/attempt/events.jsonl","rollout":"evidence/attempt/rollout.jsonl","answer":"evidence/attempt/answer.txt",
                 "process":"evidence/attempt/process.json","original_result":"result.json","original_delivery":"evidence/attempt/delivery.json",
                 "original_audit":"evidence/tool-audit.json","prompt":"evidence/prompt.txt","reservation":"reservation.json"}
        bindings = {key:{"path":str(base/name),"bytes":len((base/name).read_bytes()),"sha256":f.sha((base/name).read_bytes())} for key,name in names.items()}
        bindings["manifest"]={"path":str(self.root/"manifest.json"),"bytes":len((self.root/"manifest.json").read_bytes()),"sha256":f.sha((self.root/"manifest.json").read_bytes())}
        f.write(self.receipt,p._pretty({"schema":"sshai-benchmark/issue10-local-live-qualification-1","phase_manifest_digest":manifest["digest"],"slot":slot,
            "source_bindings":bindings,"source_contract":continuation.SOURCE_CONTRACT,"source_references":continuation.PRODUCER_SOURCES,
            "completion":json.loads((base/"evidence/completion-evidence.json").read_bytes()),
            "usage":json.loads((base/"evidence/capture-report.json").read_bytes())["usage"],"status":"independently_established_final"}))
        self.expected_digest = patch.object(continuation,"PREDECESSOR_DIGEST",manifest["digest"])
        self.expected_digest.start();self.addCleanup(self.expected_digest.stop)
        return manifest

    def continue_plan(self, name="patch-continuation"):
        new = self.private / name
        with patch.object(p,"EXPECTED_CODEX_PATH",self.codex), patch.object(s,"_BINARY_PROBE",self.fake_probe):
            manifest = s.prepare_patch_continuation(new,self.root,self.snapshot7,qualification=self.receipt,
                    reason="synthetic intercepted lifecycle coverage",authorization_note="synthetic authorized continuation")
        return new,manifest

    def test_full_lineage_preserves_all_outcomes_and_only_5_to_36_runs(self):
        old = self.retained()
        before = {str(path):f.sha(path.read_bytes()) for root in (self.original,self.root) for path in root.rglob('*') if path.is_file() and not path.is_symlink() and 'auth' not in path.parts}
        previous = self.root
        new, manifest = self.continue_plan()
        self.assertEqual(manifest["slots"],old["slots"])
        self.assertEqual(manifest["config"],old["config"])
        self.assertNotIn("capture_recovery",manifest)
        self.assertEqual(manifest["patch_continuation"]["inherited_slots"],[1,2,3,4])
        self.assertEqual(before,{name:f.sha(Path(name).read_bytes()) for name in before})
        with patch.object(p,"EXPECTED_CODEX_PATH",self.codex):
            summary=s.summarize(new)
        self.assertEqual(len(summary["slots"]),36)
        self.assertFalse(summary["slots"][2]["continuation"]["allowed"])
        self.assertFalse(summary["slots"][3]["continuation"]["allowed"])
        self.root=new
        approval=self.approve(manifest)
        with self.assertRaises(p.PilotInputError):self.run_slot(5,approval)
        self.ready()
        for number in (1,2,3,4,6):
            with self.assertRaises(p.PilotInputError):self.run_slot(number,approval)
        for number in range(5,37):
            self.assertEqual(self.run_slot(number,approval)["execution"],"completed")
            with self.assertRaises(p.PilotInputError):self.run_slot(number,approval)
        self.root=previous
        with self.assertRaises(p.PilotInputError):self.continue_plan("duplicate")

    def test_tampering_ownership_receipt_source_ancestor_or_supplementary_blocks(self):
        self.retained(); previous=self.root
        new,manifest=self.continue_plan();self.root=new;self.ready();approval=self.approve(manifest)
        for path in (previous/"patch-continuation-owner.json",self.receipt,self.snapshot7/"scripts/benchmark_issue10_local_series.py",
                     self.original/"slots/001/result.json",previous/"slots/004/evidence/attempt/rollout.jsonl",
                     previous/"readiness/result.json",new/"continuation/slot-004/tool-audit.json"):
            data=path.read_bytes();mode=path.stat().st_mode&0o777
            path.chmod(0o600)
            f.write(path,data+b"tamper")
            with self.assertRaises((p.PilotInputError,ValueError)):self.run_slot(5,approval)
            f.write(path,data,mode)
        self.assertFalse((new/"slots/005").exists())

    def test_prospective_collection_accepts_only_profile_then_future_unknown_stops(self):
        self.retained();new,manifest=self.continue_plan();self.root=new;self.ready();approval=self.approve(manifest)
        result=self.run_slot(5,approval,collector=self.patch_collector)
        self.assertTrue(result["continuation"]["allowed"])
        audit=json.loads((new/"slots/005/evidence/tool-audit.json").read_bytes())
        self.assertEqual(audit["status"],"bounded-recorded")
        self.assertEqual(audit["semantic_routing"]["status"],"unknown")
        self.inject_unknown=True
        result=self.run_slot(6,approval,collector=self.patch_collector)
        self.assertFalse(result["continuation"]["allowed"])
        with self.assertRaises(p.PilotInputError):self.run_slot(7,approval)
        self.assertFalse((new/"slots/007").exists())

    def test_receipt_status_never_substitutes_for_bound_raw_evidence(self):
        self.retained()
        value=json.loads(self.receipt.read_bytes());value["status"]="unknown";value["approved"]=False
        f.write(self.receipt,p._pretty(value))
        # Eligibility is the independently replayed source-bound lifecycle proof;
        # receipt status is retained provenance, not a Boolean launch decision.
        new,manifest=self.continue_plan()
        self.assertEqual(manifest["patch_continuation"]["qualification"]["sha256"],f.sha(self.receipt.read_bytes()))
        self.assertFalse(json.loads((new/"approval-template.json").read_bytes())["approved"])

    def test_failed_fresh_readiness_and_deeper_lineage_refuse(self):
        self.retained()
        new,manifest=self.continue_plan();self.root=new
        approval=self.approve(manifest)
        def denied(*args):
            raise p.PilotInputError("synthetic denied canary")
        with patch.object(p,"EXPECTED_CODEX_PATH",self.codex),patch.object(s,"_ACCESS_QUALIFIER",denied):
            with self.assertRaises(p.PilotInputError):s.preflight(new)
        with self.assertRaises(p.PilotInputError):self.run_slot(5,approval)
        with self.assertRaises(p.PilotInputError):self.ready()
        with self.assertRaises(p.PilotInputError):self.continue_plan("deeper-lineage")
        self.assertFalse((new/"slots/005").exists())
        self.assertFalse((new/"patch-continuation-owner.json").exists())

    def test_missing_or_extra_prefix_and_foreign_receipt_fail_without_owner(self):
        self.retained()
        (self.root/"slots/005").mkdir()
        with self.assertRaises(p.PilotInputError):self.continue_plan()
        (self.root/"slots/005").rmdir()
        value=json.loads(self.receipt.read_bytes());value["source_bindings"]["answer"]["sha256"]="0"*64
        f.write(self.receipt,p._pretty(value))
        with self.assertRaises(p.PilotInputError):self.continue_plan()
        self.assertFalse((self.root/"patch-continuation-owner.json").exists())


if __name__ == "__main__": unittest.main()
