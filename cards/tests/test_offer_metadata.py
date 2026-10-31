"""Synthetic tiles: no account data or bank access."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'bin'))
import cards_journal


class OfferMetadataTests(unittest.TestCase):
    def test_chase_newlines_badges_and_time_remaining(self):
        self.assertEqual(cards_journal.parse_chase_offer('Expiring soon\nNew\nOn Inc\n10% cash back\n21d left'),
                         {'merchant': 'On Inc', 'deal': '10% cash back'})
        self.assertEqual(cards_journal.parse_chase_offer('Example\n$1.50 cash back\n8d left')['deal'], '$1.50 cash back')

    def test_chase_incomplete_or_literal_escaped_text_rejected(self):
        for text in ('On Inc', r'On Inc\n10% cash back', 'On Inc\nDetails', 'New\nOn Inc\n21d left'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                cards_journal.parse_chase_offer(text)

    def test_chase_confirmation_requires_exact_offer_and_card(self):
        probe = {'url': 'https://secure.chase.com/web/auth/dashboard#/dashboard/merchantOffers/offer-activated/FIGG:1901579?accountId=example',
                 'body': 'On Inc\nSuccess\nAdded to card\n10% cash back\nPay with Prime Visa (...0000)'}
        self.assertTrue(cards_journal.chase_activation_verified(probe, 'FIGG:1901579', 'Prime Visa (...0000)'))
        self.assertFalse(cards_journal.chase_activation_verified(probe, 'FIGG:190157', 'Prime Visa (...0000)'))
        self.assertFalse(cards_journal.chase_activation_verified(probe, 'FIGG:1901579', 'Sapphire Reserve (...1111)'))
        probe['body'] = 'Loading Prime Visa (...0000)'
        self.assertFalse(cards_journal.chase_activation_verified(probe, 'FIGG:1901579', 'Prime Visa (...0000)'))

    def test_incomplete_metadata_stops_before_click(self):
        for text in ('', 'NEW\nExample Shop', 'Example Shop\nDetails'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                cards_journal.parse_amex_offer(text)

    def test_plain_and_multiple_badges(self):
        for prefix in ('', 'New\nExclusive\n'):
            self.assertEqual(cards_journal.parse_amex_offer(prefix + 'Example\n10% back'),
                             {'merchant': 'Example', 'deal': '10% back'})

    def test_badge_is_not_merchant(self):
        parse = getattr(cards_journal, 'parse_amex_offer', None)
        self.assertTrue(callable(parse), 'Missing maintained pre-click Amex parser')
        self.assertEqual(parse('NEW\nExample Shop\nSpend $50, get $10 back\nDetails'),
                         {'merchant': 'Example Shop', 'deal': 'Spend $50, get $10 back'})
