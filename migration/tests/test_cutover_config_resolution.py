from django.test import TestCase

from migration.cutover.config_resolution import resolve_cutover_config
from migration.models import CutoverConfig, CutoverMode


class ResolveCutoverConfigTest(TestCase):
    def test_falls_back_to_global_when_no_specific_scope_matches(self):
        global_cfg = CutoverConfig.objects.create(scope="global", updated_by="admin")
        resolved = resolve_cutover_config("/share/finance/reports")
        self.assertEqual(resolved.id, global_cfg.id)

    def test_most_specific_scope_wins_over_global(self):
        CutoverConfig.objects.create(scope="global", mode=CutoverMode.SHADOW_WRITE, updated_by="admin")
        specific = CutoverConfig.objects.create(
            scope="/share/finance", mode=CutoverMode.HARD_FREEZE, updated_by="admin"
        )
        resolved = resolve_cutover_config("/share/finance/reports")
        self.assertEqual(resolved.id, specific.id)

    def test_inactive_config_is_ignored(self):
        CutoverConfig.objects.create(
            scope="/share/finance", active=False, mode=CutoverMode.HARD_FREEZE, updated_by="admin"
        )
        global_cfg = CutoverConfig.objects.create(scope="global", updated_by="admin")
        resolved = resolve_cutover_config("/share/finance/reports")
        self.assertEqual(resolved.id, global_cfg.id)

    def test_no_matching_config_returns_none(self):
        self.assertIsNone(resolve_cutover_config("/share/finance/reports"))
