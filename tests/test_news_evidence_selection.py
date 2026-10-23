import copy
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

from tools.agents.lib.news_evidence_comparison import compare
from tools.agents.lib.news_evidence_selection import encode, select

ROOT = Path(__file__).resolve().parents[1]
make_cases = runpy.run_path(str(Path(__file__).parent / 'fixtures/build_news_evidence_cases.py'))['make_cases']


class EvidenceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.case = make_cases()[0]

    def test_twenty_cases_keep_exact_gold_and_never_promote_synthetic(self):
        cases = make_cases()
        before = encode(cases)
        report = compare(cases, max_context_chars=6000)
        self.assertEqual(before, encode(cases))
        self.assertEqual(20, report['case_count'])
        self.assertEqual(1, report['required_evidence_retention'])
        self.assertEqual(0, report['additional_correction_counterargument_omissions'])
        self.assertIsNone(report['input_token_reduction'])
        self.assertFalse(report['promotion_eligible'])
        self.assertIn('human_required_evidence_not_complete', report['remaining_gaps'])
        for case in cases:
            result = select(case, max_context_chars=6000)
            self.assertEqual(result, select(case, max_context_chars=6000))
            self.assertEqual(result['status'], 'ready_for_comparison')
            originals = {d['source_id']: d['text'] for d in case['documents']}
            for passage in result['selected_context']['evidence']:
                self.assertEqual(originals[passage['source_id']][passage['start']:passage['end']], passage['excerpt'])

    def test_cutoff_checks_publication_availability_and_metadata(self):
        for field in ('published_at', 'available_at', 'metadata_as_of'):
            case = copy.deepcopy(self.case)
            case['documents'][0][field] = '2026-09-02T00:00:00+09:00'
            result = select(case)
            self.assertNotIn(case['documents'][0]['source_id'], result['eligible_source_ids'])
        for value in (None, '2026-09-01', '2026-09-01T08:00:00'):
            case = copy.deepcopy(self.case)
            case['documents'][0]['published_at'] = value
            self.assertNotIn(case['documents'][0]['source_id'], select(case)['eligible_source_ids'])
        # UTC equivalent must be eligible; no lexical timestamp comparison.
        self.case['documents'][0]['published_at'] = '2026-08-31T23:00:00Z'
        self.assertIn(self.case['documents'][0]['source_id'], select(self.case)['eligible_source_ids'])

    def test_budget_cannot_silently_drop_correction_or_counterargument(self):
        result = select(self.case, max_context_chars=10)
        self.assertEqual(result['status'], 'blocked')
        kinds = {p['stance'] for p in result['selected_context']['evidence']}
        self.assertTrue({'correction', 'counterargument'} <= kinds)
        self.assertIn('protected_or_claim_evidence_exceeds_budget', result['reasons'])

    def test_duplicate_ids_cannot_replace_an_original_version(self):
        self.case['documents'].append(copy.deepcopy(self.case['documents'][0]))
        self.assertEqual(3, len(select(self.case)['eligible_source_ids']))
        self.case['documents'][-1]['text'] = 'modified after collection'
        with self.assertRaises(ValueError):
            select(self.case)

    def test_unresolved_claim_and_bad_gold_never_pass(self):
        self.case['claims'][0]['keywords'] = ['not_in_any_document']
        result = select(self.case)
        self.assertEqual(['demand'], result['unresolved_claim_ids'])
        self.assertEqual('blocked', result['status'])
        self.case['required_evidence'][0]['quote'] = 'absent gold quote'
        report = compare([self.case])
        self.assertFalse(report['cases'][0]['gold_valid'])
        self.assertIsNone(report['required_evidence_retention'])
        self.assertFalse(report['promotion_eligible'])

    def test_measurements_alone_cannot_claim_human_or_historical_validation(self):
        # Character counter is a test double, not a model token measurement.
        report = compare(make_cases(), max_context_chars=6000,
                         token_counter=len, tokenizer_name='test-character-counter')
        self.assertGreaterEqual(report['input_token_reduction'], .30)
        self.assertFalse(report['promotion_eligible'])
        self.assertIn('historical_cases_not_complete', report['remaining_gaps'])

    def test_shadow_cli_preserves_input_and_refuses_latest_or_existing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'cases.json'; output = root / 'report.json'
            source.write_text(encode(make_cases()))
            before = source.read_bytes()
            cmd = [sys.executable, str(ROOT / 'tools/agents/news_evidence_selection_agent.py'),
                   '--input', str(source), '--compare', '--max-context-chars', '6000', '--output', str(output)]
            completed = subprocess.run(cmd, text=True, capture_output=True, cwd=tmp)
            self.assertEqual(2, completed.returncode, completed.stderr)
            report = json.loads(output.read_text())
            self.assertFalse(report['promotion_eligible'])
            self.assertEqual(before, source.read_bytes())
            again = subprocess.run(cmd, text=True, capture_output=True)
            self.assertEqual(2, again.returncode)
            cmd[-1] = str(root / 'news_latest.json')
            self.assertEqual(2, subprocess.run(cmd, capture_output=True).returncode)
            self.assertFalse((root / 'news_latest.json').exists())
            self.assertEqual({'cases.json', 'report.json'}, {p.name for p in root.iterdir()})


if __name__ == '__main__':
    unittest.main()
