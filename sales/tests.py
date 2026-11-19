import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Customer, Item
from sales.models import Order, OrderLine

User = get_user_model()


class SalesOrderTests(TestCase):
    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.salesman_a = User.objects.get(username='salesman_a')
        self.salesman_b = User.objects.get(username='salesman_b')
        self.retailer_a = Customer.objects.get(name='Retailer A')
        self.distributor_b = Customer.objects.get(name='Distributor B')
        self.murukku = Item.objects.get(name='Murukku 200 g')

        self.client_a = Client()
        self.client_a.login(username='salesman_a', password='demo1234')

        self.client_b = Client()
        self.client_b.login(username='salesman_b', password='demo1234')

    def test_salesman_a_cannot_see_salesman_b_orders(self):
        """salesman_a cannot see salesman_b's orders in list or detail views."""
        # Create an order for salesman A
        order_a = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_SUBMITTED,
        )
        OrderLine.objects.create(
            order=order_a,
            item=self.murukku,
            qty=Decimal('50'),
            price_paise=5000,
        )

        # Create an order for salesman B
        order_b = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.distributor_b,
            salesman=self.salesman_b,
            status=Order.STATUS_SUBMITTED,
        )
        OrderLine.objects.create(
            order=order_b,
            item=self.murukku,
            qty=Decimal('100'),
            price_paise=5000,
        )

        # Salesman A checks order list
        response_a = self.client_a.get(reverse('order_list'))
        self.assertEqual(response_a.status_code, 200)
        orders_in_context = response_a.context['orders']
        self.assertIn(order_a, orders_in_context)
        self.assertNotIn(order_b, orders_in_context)

        # Salesman A cannot view salesman B's order detail
        response_a_detail_b = self.client_a.get(reverse('order_detail', args=[order_b.pk]))
        self.assertEqual(response_a_detail_b.status_code, 403)

        # Salesman B checks order list
        response_b = self.client_b.get(reverse('order_list'))
        self.assertEqual(response_b.status_code, 200)
        orders_in_b_context = response_b.context['orders']
        self.assertIn(order_b, orders_in_b_context)
        self.assertNotIn(order_a, orders_in_b_context)

        # Salesman B cannot view salesman A's order detail
        response_b_detail_a = self.client_b.get(reverse('order_detail', args=[order_a.pk]))
        self.assertEqual(response_b_detail_a.status_code, 403)

    def test_submitting_same_client_uuid_twice_creates_only_one_order(self):
        """submitting the same client_uuid twice creates only one order."""
        client_uuid = uuid.uuid4()
        post_data = {
            'client_uuid': str(client_uuid),
            'customer': self.retailer_a.id,
            'item_id': [str(self.murukku.id)],
            'qty': ['10'],
            'price_paise': ['5000'],
            'note': 'Test duplicate submit',
        }

        # First submission
        res1 = self.client_a.post(reverse('order_create'), post_data)
        self.assertEqual(res1.status_code, 302)
        self.assertEqual(Order.objects.filter(client_uuid=client_uuid).count(), 1)

        # Second submission with exact same client_uuid
        res2 = self.client_a.post(reverse('order_create'), post_data)
        self.assertEqual(res2.status_code, 302)
        # Verify still exactly one order exists
        self.assertEqual(Order.objects.filter(client_uuid=client_uuid).count(), 1)

        # Also verify database uniqueness constraint blocks direct model duplicates
        with self.assertRaises(IntegrityError):
            Order.objects.create(
                client_uuid=client_uuid,
                customer=self.retailer_a,
                salesman=self.salesman_a,
            )

    def test_150_packet_order_for_retailer_a_saves_correct_total(self):
        """a 150 packet order for Retailer A saves with the correct total (150 x 5000 paise = 750000)."""
        # Test submission through the view where price is pre-filled from item selling_price_paise (5000)
        client_uuid = uuid.uuid4()
        post_data = {
            'client_uuid': str(client_uuid),
            'customer': self.retailer_a.id,
            'item_id': [str(self.murukku.id)],
            'qty': ['150'],
            'price_paise': [''],  # Empty to test auto pre-fill from item.selling_price_paise
        }
        res = self.client_a.post(reverse('order_create'), post_data)
        self.assertEqual(res.status_code, 302)

        order = Order.objects.get(client_uuid=client_uuid)
        self.assertEqual(order.customer, self.retailer_a)
        self.assertEqual(order.salesman, self.salesman_a)
        self.assertEqual(order.status, Order.STATUS_SUBMITTED)

        line = order.lines.first()
        self.assertEqual(line.item, self.murukku)
        self.assertEqual(line.qty, Decimal('150'))
        self.assertEqual(line.price_paise, 5000)
        self.assertEqual(line.total_paise, 750000)

        # Order total must equal 750000 paise
        self.assertEqual(order.total_paise, 750000)
        self.assertEqual(order.total, 750000)
        self.assertEqual(order.total_rupees, "7500.00")

    def test_salesman_home_cards_present(self):
        """Home page has 'New Order' and 'My Orders' cards/links."""
        response = self.client_a.get(reverse('salesman_home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(reverse('order_create'), content)
        self.assertIn(reverse('order_list'), content)
        self.assertIn("New Order", content)
        self.assertIn("My Orders", content)
