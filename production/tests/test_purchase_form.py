from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.ledger import get_balance
from core.models import Item, Supplier, Unit
from production.models import Purchase, PurchaseLine
from production.views import PurchaseLineForm

User = get_user_model()


class PurchaseFormTest(TestCase):
    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.flour = Item.objects.get(name='Flour')
        self.kg = Unit.objects.get(code='kg')
        self.supplier = Supplier.objects.get(name='Demo Flour Mills')
        self.user = User.objects.get(username='prod1')
        self.client.login(username='prod1', password='demo1234')

    def test_purchase_form_field_label(self):
        """The rate field is labeled 'Rate (₹ per unit)'."""
        form = PurchaseLineForm()
        self.assertEqual(form.fields['rate'].label, "Rate (₹ per unit)")

        response = self.client.get(reverse('purchase_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rate (₹ per unit)")
        self.assertContains(response, "Line Total")
        self.assertContains(response, "Purchase Total:")

    def test_purchase_60kg_at_45_rupees(self):
        """
        60 kg at ₹45 gives unit cost 4500 paise, purchase total 270000 paise, shown as ₹2,700.00.
        """
        post_data = {
            'supplier': self.supplier.pk,
            'date': date.today().isoformat(),
            'note': 'Test purchase 60kg at ₹45',
            'form-TOTAL_FORMS': '1',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '10',
            'form-0-item': self.flour.pk,
            'form-0-qty': '60',
            'form-0-purchase_unit': self.kg.pk,
            'form-0-rate': '45',
        }
        response = self.client.post(reverse('purchase_create'), post_data)
        self.assertRedirects(response, reverse('purchase_list'))

        purchase = Purchase.objects.latest('id')
        line = purchase.lines.first()

        # 60 kg at ₹45 gives unit cost 4500 paise, purchase total 270000 paise
        self.assertEqual(line.unit_cost_paise, 4500)
        self.assertEqual(purchase.total_paise, 270000)
        self.assertEqual(line.line_total_paise, 270000)

        # Shown as ₹2,700.00
        list_response = self.client.get(reverse('purchase_list'))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "₹2,700.00")

        # Also verify ledger balance updated (100 opening + 60 purchased = 160)
        self.assertEqual(get_balance(self.flour), Decimal('160'))

    def test_purchase_decimal_rate_conversion(self):
        """Entering rate as 45.50 converts to integer paise using Decimal with round half up."""
        post_data = {
            'supplier': self.supplier.pk,
            'date': date.today().isoformat(),
            'note': 'Test decimal rate',
            'form-TOTAL_FORMS': '1',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '10',
            'form-0-item': self.flour.pk,
            'form-0-qty': '2',
            'form-0-purchase_unit': self.kg.pk,
            'form-0-rate': '45.50',
        }
        response = self.client.post(reverse('purchase_create'), post_data)
        self.assertRedirects(response, reverse('purchase_list'))

        purchase = Purchase.objects.latest('id')
        line = purchase.lines.first()
        self.assertEqual(line.unit_cost_paise, 4550)
        self.assertEqual(purchase.total_paise, 9100)
        self.assertEqual(line.line_total_paise, 9100)

        list_response = self.client.get(reverse('purchase_list'))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "₹91.00")
