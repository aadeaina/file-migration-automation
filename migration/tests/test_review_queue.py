from django.test import TestCase

from migration.discovery.types import ACE
from migration.models import MappingReviewQueueEntry
from migration.permission_mapping.loader import load_mapping_table
from migration.permission_mapping.review_queue import enqueue_review_flags
from migration.permission_mapping.translate import translate_ace

TABLE = load_mapping_table()


class ReviewQueueTest(TestCase):
    def test_non_exact_confidence_is_queued_once_per_pattern(self):
        ace = ACE(
            identity="DOMAIN\\jane.doe",
            rights=frozenset({"TakeOwnership"}),
            allow=True,
            inherited=False,
        )
        result = translate_ace(ace, "file", TABLE)

        enqueue_review_flags(result.review_flags)
        enqueue_review_flags(result.review_flags)  # second encounter of same pattern

        self.assertEqual(MappingReviewQueueEntry.objects.count(), 1)
        entry = MappingReviewQueueEntry.objects.get()
        self.assertEqual(entry.ntfs_right, "TakeOwnership")
        self.assertEqual(entry.object_type, "file")
        self.assertEqual(entry.confidence, "policy_decision")
        self.assertEqual(entry.mapping_table_version, TABLE.version)

    def test_exact_confidence_never_queued(self):
        ace = ACE(
            identity="DOMAIN\\jane.doe",
            rights=frozenset({"ReadData"}),
            allow=True,
            inherited=False,
        )
        result = translate_ace(ace, "file", TABLE)

        enqueue_review_flags(result.review_flags)

        self.assertEqual(MappingReviewQueueEntry.objects.count(), 0)
