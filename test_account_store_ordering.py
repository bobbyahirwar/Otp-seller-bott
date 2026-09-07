import os
import unittest
from unittest.mock import patch

os.environ.setdefault("API_ID", "1")
os.environ.setdefault("API_HASH", "test-hash")
os.environ.setdefault("BOT_TOKEN", "1:test-token")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("FAMAPP_UPI_ID", "famapp@example")
os.environ.setdefault("FAMAPP_PAYEE_NAME", "Test Payee")
os.environ.setdefault("IMAP_USERNAME", "test@example.com")
os.environ.setdefault("IMAP_APP_PASSWORD", "test-password")

from test_mongo_persistence import FakeDatabase
import mongo_persistence
from mongo_persistence import MongoRepository, MongoRuntimeStore


class FakeMongoClient:
    def __init__(self):
        self.database = FakeDatabase()
        self.admin = self

    def command(self, _name):
        return {"ok": 1}

    def __getitem__(self, _name):
        return self.database


with patch.object(mongo_persistence, "create_mongo_client", return_value=FakeMongoClient()):
    import james


class AccountStoreOrderingTests(unittest.TestCase):
    def setUp(self):
        self.database = FakeDatabase()
        self.store = MongoRuntimeStore(MongoRepository(self.database))
        self.store.repository.prepare()
        self.previous_store = james.mongo_store
        james.mongo_store = self.store

    def tearDown(self):
        james.mongo_store = self.previous_store

    @staticmethod
    def product(country, category, year, price, dc):
        return {
            "country": country,
            "category": category,
            "year": year,
            "price": price,
            "dc": dc,
            "stock": 1,
        }

    def test_ordered_collection_keeps_message_and_button_product_together(self):
        spammed = self.product("USA", "Spammed", 2025, 20, "dc5")
        non_spam = self.product("USA", "Non Spam", 2025, 39, "dc4")

        ordered = james.order_account_store_products([spammed, non_spam])

        self.assertEqual([item["category"] for item in ordered], ["Spammed", "Non Spam"])
        self.assertEqual(
            [james.get_product_token(item) for item in ordered],
            [james.get_product_token(spammed), james.get_product_token(non_spam)],
        )

    def test_reorder_persists_across_store_restart_and_appends_or_removes_groups(self):
        spammed = self.product("USA", "Spammed", 2025, 20, "dc5")
        non_spam = self.product("USA", "Non Spam", 2025, 39, "dc4")
        premium = self.product("USA", "Premium", 2025, 50, "dc1")
        spammed_id = james.get_product_token(spammed)
        non_spam_id = james.get_product_token(non_spam)
        premium_id = james.get_product_token(premium)

        james.set_account_store_order([non_spam_id, spammed_id])
        ordered = james.order_account_store_products([spammed, non_spam])
        self.assertEqual([item["category"] for item in ordered], ["Non Spam", "Spammed"])

        james.mongo_store = MongoRuntimeStore(MongoRepository(self.database))
        ordered_after_restart = james.order_account_store_products([spammed, non_spam])
        self.assertEqual(
            [item["category"] for item in ordered_after_restart],
            ["Non Spam", "Spammed"],
        )

        ordered_with_new = james.order_account_store_products([spammed, non_spam, premium])
        self.assertEqual(
            [item["category"] for item in ordered_with_new],
            ["Non Spam", "Spammed", "Premium"],
        )
        self.assertEqual(james.get_account_store_order(), [non_spam_id, spammed_id, premium_id])

        ordered_after_removal = james.order_account_store_products([non_spam, premium])
        self.assertEqual(
            [item["category"] for item in ordered_after_removal],
            ["Non Spam", "Premium"],
        )
        self.assertEqual(james.get_account_store_order(), [non_spam_id, premium_id])

    def test_dc_does_not_change_stable_group_callback_identity(self):
        first = self.product("India", "Spammed", 2025, 15, "dc5")
        second = dict(first, dc="dc1")

        self.assertEqual(james.get_product_token(first), james.get_product_token(second))
        james.set_account_store_order([james.get_product_token(first)])
        ordered = james.order_account_store_products([second])
        self.assertEqual(james.get_product_token(ordered[0]), james.get_product_token(first))


if __name__ == "__main__":
    unittest.main()
