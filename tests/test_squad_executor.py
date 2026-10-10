import os
import tempfile
import unittest
from src.models import ChildCard, MotherCard
from src.squad_executor import SquadDAGScheduler, VRAMHardwareSentinel, SquadWaveExecutor
from src.git_worktree_manager import GitWorktreeManager


class TestSquadExecutor(unittest.TestCase):
    def test_dag_wave_partitioning(self):
        c1 = ChildCard(id="c1", parent_mother_id="m1", name="Card 1", tactical_objective="T1", depends_on=[])
        c2 = ChildCard(id="c2", parent_mother_id="m1", name="Card 2", tactical_objective="T2", depends_on=[])
        c3 = ChildCard(id="c3", parent_mother_id="m1", name="Card 3", tactical_objective="T3", depends_on=["c1"])
        c4 = ChildCard(id="c4", parent_mother_id="m1", name="Card 4", tactical_objective="T4", depends_on=["c3"])

        waves = SquadDAGScheduler.partition_into_waves([c1, c2, c3, c4])

        self.assertEqual(len(waves), 3)
        wave_1_ids = [c.id for c in waves[0]]
        wave_2_ids = [c.id for c in waves[1]]
        wave_3_ids = [c.id for c in waves[2]]

        self.assertIn("c1", wave_1_ids)
        self.assertIn("c2", wave_1_ids)
        self.assertEqual(wave_2_ids, ["c3"])
        self.assertEqual(wave_3_ids, ["c4"])

    def test_vram_sentinel(self):
        sentinel = VRAMHardwareSentinel(memory_threshold_pct=99.9)
        pct = sentinel.get_memory_utilization_pct()
        self.assertGreaterEqual(pct, 0.0)
        self.assertLessEqual(pct, 100.0)
        self.assertFalse(sentinel.should_throttle_to_serial())

    def test_worktree_manager_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = GitWorktreeManager(workspace_root=tmpdir)
            wt_path = mgr.create_worktree(branch_name="aje/test-worktree")
            self.assertTrue(os.path.exists(wt_path))

            cleaned = mgr.cleanup_worktree(wt_path)
            self.assertTrue(cleaned)
            self.assertFalse(os.path.exists(wt_path))


if __name__ == "__main__":
    unittest.main()
