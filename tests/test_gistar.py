import unittest
from unittest.mock import patch
import numpy as np
from services import gistar_preprocess as gp
from services import hotspot_service as hs

class GiStarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mines = hs.load_geojson()
        cls.grid = gp.make_grid(cls.mines, 500)
        cls.mine = [c for c in cls.grid if c['properties']['mine_id'] == cls.mines[0]['mine_id']]
    def test_grid_stable_and_all_mines(self):
        self.assertEqual(self.grid, gp.make_grid(self.mines, 500))
        self.assertEqual(len({c['properties']['mine_id'] for c in self.grid}), 4)
        self.assertEqual(len({c['properties']['cell_id'] for c in self.grid}), len(self.grid))
        a = gp.calculate(self.mine, 2000, {}, .8)
        b = gp.calculate(self.mine, 2005, {}, .8)
        self.assertEqual([c['geometry'] for c in a], [c['geometry'] for c in b])
    def test_weights_include_self_and_never_cross_mines(self):
        w = gp.queen_weights(self.mine)
        for i, neighbors in w.neighbors.items():
            self.assertIn(i, neighbors)
            for j in neighbors:
                self.assertEqual(self.mine[i]['properties']['mine_id'], self.mine[j]['properties']['mine_id'])
                self.assertLessEqual(abs(self.mine[i]['properties']['grid_x']-self.mine[j]['properties']['grid_x']), 1)
    def test_categories_and_adjustment(self):
        self.assertEqual(gp.category(2.5, .01), 'hotspot_99')
        self.assertEqual(gp.category(-2, .05), 'coldspot_95')
        self.assertEqual(gp.category(-2, .10), 'coldspot_90')
        self.assertEqual(gp.category(0, .5), 'not_significant')
        self.assertEqual(gp.category(None, None), 'unavailable')
        np.testing.assert_allclose(gp.adjust_bh([.01,.04,.03]), [.03,.04,.04])
    def test_missing_zero_variance_and_fraction(self):
        values = {c['properties']['cell_id']: {'mining_area': 20, 'valid_area': 100, 'total_area': 100} for c in self.mine}
        values[self.mine[0]['properties']['cell_id']]['valid_area'] = 0
        result = gp.calculate(self.mine, 2010, values, .8)
        self.assertIsNone(result[0]['properties']['mining_fraction'])
        self.assertEqual(result[1]['properties']['mining_fraction'], .2)
        self.assertEqual(result[1]['properties']['reason'], 'insufficient_cells_or_zero_variance')
        self.assertTrue(all(f['properties']['category'] == 'unavailable' for f in result))
    def test_actual_statistics_calculated(self):
        values = {c['properties']['cell_id']: {'mining_area': i % 7, 'valid_area': 100, 'total_area': 100} for i,c in enumerate(self.mine)}
        result = gp.calculate(self.mine, 2015, values, .8)
        self.assertTrue(any(f['properties']['z_score'] is not None for f in result))
        self.assertTrue(all(f['properties']['adjusted_p_value'] is not None for f in result if f['properties']['z_score'] is not None))
    def test_single_response_missing_precomputation(self):
        with patch.dict('os.environ', {'GISTAR_RESULTS_PATH':'/nonexistent/file'}): result = hs.get_analysis()
        self.assertEqual(result['hotspot_status'], 'not_computed')
        self.assertEqual(result['hotspot_results']['features'], [])
        self.assertEqual(len(result['mines']), 4)
        self.assertEqual(result['years'], list(hs.YEARS))
        self.assertEqual(len(result['legends']['hotspot']), 8)

if __name__ == '__main__': unittest.main()

class BackgroundAnalysisTests(unittest.TestCase):
    def setUp(self):
        hs._JOB.update(status='idle', error=None)
    def tearDown(self):
        hs._JOB.update(status='idle', error=None)

    @patch('services.hotspot_service.threading.Thread')
    @patch('services.hotspot_service.get_ee_credentials')
    def test_session_starts_one_isolated_job(self, auth, thread):
        auth.return_value.to_json.return_value = '{"refresh_token":"secret"}'
        with patch.dict('os.environ', {'GISTAR_RESULTS_PATH':'/nonexistent/result'}):
            first = hs.get_analysis('session')
            second = hs.get_analysis('session')
            polled = hs.get_analysis()
        self.assertEqual(first['job']['status'], 'running')
        self.assertEqual(second['job']['status'], 'running')
        self.assertEqual(polled['job']['status'], 'running')
        self.assertEqual(auth.call_count, 1)
        self.assertEqual(thread.call_count, 1)
        self.assertNotIn('secret', str(first))

    @patch('services.hotspot_service.threading.Thread')
    @patch('services.hotspot_service.get_ee_credentials', side_effect=hs.HTTPException(401, 'Not authenticated'))
    def test_invalid_session_does_not_start_job(self, auth, thread):
        with patch.dict('os.environ', {'GISTAR_RESULTS_PATH':'/nonexistent/result'}), self.assertRaises(hs.HTTPException):
            hs.get_analysis('expired')
        thread.assert_not_called()
        self.assertEqual(hs._JOB['status'], 'idle')

    @patch('services.hotspot_service.subprocess.run')
    def test_job_failure_is_reported(self, run):
        run.return_value.returncode = 1
        run.return_value.stderr = 'ModuleNotFoundError: spatial library'
        hs._run_job('{}', __import__('pathlib').Path('/nonexistent/result'))
        self.assertEqual(hs._JOB['status'], 'failed')
        self.assertIn('failed', hs._JOB['error'])
        self.assertEqual(run.call_args.kwargs['input'], '{}')
        self.assertNotIn('{}', run.call_args.args[0])

class WorkerDiagnosticsTests(unittest.TestCase):
    def test_sensitive_values_are_redacted(self):
        data = '{"token":"secret-access", "refresh_token":"secret-refresh", "client_secret":"secret-client"}'
        redacted = hs._redact_worker_output('secret-access secret-refresh secret-client failure', data)
        self.assertNotIn('secret-', redacted)
        self.assertIn('failure', redacted)

    def test_invalid_credentials_never_echo_output(self):
        self.assertEqual(hs._redact_worker_output('sensitive content', 'invalid'), 'Worker diagnostics unavailable.')

class SessionCredentialsTests(unittest.TestCase):
    def test_access_token_without_refresh_token(self):
        credentials = gp.credentials_from_session({'token': 'access', 'client_id': 'id'})
        self.assertEqual(credentials.token, 'access')
        self.assertIsNone(credentials.refresh_token)

    def test_empty_credentials_rejected(self):
        with self.assertRaises(ValueError):
            gp.credentials_from_session({})
