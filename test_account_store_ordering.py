import os
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

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
            "icon": "🇺🇸" if country == "USA" else "🇮🇳",
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

    def test_admin_order_menu_shows_current_order_and_boundary_controls(self):
        first = self.product("USA", "Spammed", 2025, 20, "dc5")
        second = self.product("USA", "Non Spam", 2025, 39, "dc4")
        first_id = james.get_product_token(first)
        second_id = james.get_product_token(second)
        james.set_account_store_order([second_id, first_id])
        event = type("Event", (), {"edit": AsyncMock(), "answer": AsyncMock()})()

        with patch.object(james, "get_available_account_products", return_value=[second, first]):
            asyncio.run(james.account_store_order_menu(event))

        rendered_buttons = event.edit.await_args.kwargs["buttons"]
        labels = [row[0].text for row in (rendered_buttons[0], rendered_buttons[2])]
        self.assertIn("1. 🇺🇸 USA • Non Spam • 2025", labels[0])
        self.assertIn("2. 🇺🇸 USA • Spammed • 2025", labels[1])
        self.assertEqual(len(rendered_buttons[1]), 1)
        self.assertEqual(len(rendered_buttons[3]), 1)

    def test_admin_move_up_and_down_persist_swaps(self):
        products = [
            self.product("USA", "Spammed", 2025, 20, "dc5"),
            self.product("USA", "Non Spam", 2025, 39, "dc4"),
            self.product("India", "Spammed", 2025, 15, "dc1"),
        ]
        ids = [james.get_product_token(product) for product in products]
        james.set_account_store_order(ids)
        event = type("Event", (), {"edit": AsyncMock(), "answer": AsyncMock()})()

        ordered_products = lambda: james.order_account_store_products(products)
        with patch.object(james, "get_available_account_products", side_effect=ordered_products):
            asyncio.run(james.move_account_store_product(event, "up", ids[1], 1))
        self.assertEqual(james.get_account_store_order(), [ids[1], ids[0], ids[2]])

        with patch.object(james, "get_available_account_products", side_effect=ordered_products):
            asyncio.run(james.move_account_store_product(event, "down", ids[1], 1))
        self.assertEqual(james.get_account_store_order(), ids)

        with patch.object(james, "get_available_account_products", side_effect=ordered_products):
            asyncio.run(james.move_account_store_product(event, "up", ids[0], 1))
        self.assertEqual(james.get_account_store_order(), ids)
        with patch.object(james, "get_available_account_products", side_effect=ordered_products):
            asyncio.run(james.move_account_store_product(event, "down", ids[2], 1))
        self.assertEqual(james.get_account_store_order(), ids)

    def test_admin_account_store_messages_menu_uses_existing_order(self):
        first = self.product("USA", "Spammed", 2025, 20, "dc5")
        second = self.product("USA", "Non Spam", 2025, 39, "dc4")
        first_id = james.get_product_token(first)
        second_id = james.get_product_token(second)
        james.set_account_store_order([second_id, first_id])
        event = type("Event", (), {"edit": AsyncMock(), "answer": AsyncMock()})()

        with patch.object(james, "get_available_account_products", return_value=[second, first]):
            asyncio.run(james.account_store_messages_menu(event))

        buttons = event.edit.await_args.kwargs["buttons"]
        rendered = [button.text for row in buttons for button in row]
        self.assertIn("1️⃣ 🇺🇸 USA • Non Spam • 2025 • $0.41", rendered)
        self.assertIn("2️⃣ 🇺🇸 USA • Spammed • 2025 • $0.21", rendered)
        self.assertIn(f"✏️ Edit", rendered)

    def test_account_store_custom_message_persists_by_stable_token_and_survives_reorder(self):
        spammed = self.product("USA", "Spammed", 2025, 20, "dc5")
        non_spam = self.product("USA", "Non Spam", 2025, 39, "dc4")
        spammed_id = james.get_product_token(spammed)
        non_spam_id = james.get_product_token(non_spam)

        james.set_account_store_order([non_spam_id, spammed_id])
        james.set_account_store_messages({spammed_id: "Custom spammed message"})

        self.assertEqual(james.get_account_store_message(spammed), "Custom spammed message")
        self.assertIsNone(james.get_account_store_message(non_spam))

        james.mongo_store = MongoRuntimeStore(MongoRepository(self.database))
        self.assertEqual(james.get_account_store_messages()[spammed_id], "Custom spammed message")
        self.assertEqual(
            [item["category"] for item in james.order_account_store_products([spammed, non_spam])],
            ["Non Spam", "Spammed"],
        )

    def test_account_store_custom_message_uses_current_dynamic_placeholders(self):
        product = self.product("USA", "Spammed", 2025, 20, "dc5")
        token = james.get_product_token(product)

        james.set_account_store_messages({
            token: "📦 {country} ({condition})\n💰 {price} (₹{price_inr})\n📦 Stock: {stock}\n🖥 {data_center}"
        })

        rendered = james.render_account_store_listing_message(product)
        self.assertIn("📦 USA (Spammed)", rendered)
        self.assertIn("💰 $0.21 (₹20)", rendered)
        self.assertIn("📦 Stock: 1", rendered)
        self.assertIn("🖥 dc5", rendered)

    def test_admin_account_store_message_edit_uses_listing_message_not_purchase_details(self):
        product = self.product("USA", "Spammed", 2025, 20, "dc5")
        token = james.get_product_token(product)
        event = type(
            "Event",
            (),
            {
                "sender_id": 1,
                "chat_id": 1,
                "data": f"adm_account_store_message_edit|{token}".encode(),
                "edit": AsyncMock(),
                "answer": AsyncMock(),
            },
        )()

        with patch.object(james, "get_available_account_products", return_value=[product]):
            asyncio.run(james.admin_actions(event))

        preview = event.edit.await_args.args[0]
        self.assertIn("Current message/card text:", preview)
        self.assertIn("• USA (Spammed) (dc dc5) 2025: $0.21 (₹20) - Stock: 1", preview)
        self.assertNotIn("PRODUCT DETAILS", preview)

    def test_account_store_custom_message_does_not_change_purchase_flow_message(self):
        product = self.product("USA", "Spammed", 2025, 20, "dc5")
        token = james.get_product_token(product)

        james.set_account_store_messages({
            token: "📦 {country} ({condition})\n💰 {price} (₹{price_inr})\n📦 Stock: {stock}\n🖥 {data_center}"
        })

        rendered = james.render_account_store_product_message(product)
        self.assertIn("<b>PRODUCT DETAILS</b>", rendered)
        self.assertIn("<b>Price:</b>", rendered)
        self.assertNotIn("📦 USA (Spammed)", rendered)

    def test_account_store_default_listing_message_falls_back_to_listing_card(self):
        product = self.product("USA", "Spammed", 2025, 20, "dc5")

        rendered = james.render_account_store_listing_message(product)
        self.assertIn("• USA (Spammed) (dc dc5) 2025: $0.21 (₹20) - Stock: 1", rendered)
        self.assertNotIn("PRODUCT DETAILS", rendered)


if __name__ == "__main__":
    unittest.main()
