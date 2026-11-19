import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from core.ledger import get_balance, issue_stock, receive_stock
from core.models import AuditLog, Customer, Item
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

        self.admin_user = User.objects.get(username='admin1')
        self.admin_client = Client()
        self.admin_client.login(username='admin1', password='demo1234')

        self.owner_user = User.objects.get(username='owner1')
        self.owner_client = Client()
        self.owner_client.login(username='owner1', password='demo1234')

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

    def test_salesman_a_gets_403_on_pending_orders_page(self):
        """salesman_a gets 403 on the pending orders page; admin and owner can access."""
        # salesman_a gets 403
        response_salesman = self.client_a.get(reverse('admin_pending_orders'))
        self.assertEqual(response_salesman.status_code, 403)

        # salesman_b gets 403
        response_salesman_b = self.client_b.get(reverse('admin_pending_orders'))
        self.assertEqual(response_salesman_b.status_code, 403)

        # admin gets 200
        response_admin = self.admin_client.get(reverse('admin_pending_orders'))
        self.assertEqual(response_admin.status_code, 200)

        # owner gets 200
        response_owner = self.owner_client.get(reverse('admin_pending_orders'))
        self.assertEqual(response_owner.status_code, 200)

    def test_finished_goods_stock_238_order_150_shows_no_shortage(self):
        """with finished goods stock 238, a 150 packet order shows no shortage."""
        # Benchmark opening stock for Murukku is 50. Receive 188 to reach exactly 238.
        receive_stock(
            item=self.murukku,
            qty=Decimal('188'),
            unit_cost_paise=3000,
            source_type='production',
            source_id='test_run_238',
            user=self.admin_user,
        )
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # Create a 150 packet order for Retailer A
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_SUBMITTED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('150'),
            price_paise=5000,
        )

        # Admin opens the order detail page
        response = self.admin_client.get(reverse('admin_order_detail', args=[order.pk]))
        self.assertEqual(response.status_code, 200)

        # Order shows NO shortage
        self.assertNotContains(response, 'Shortage of')
        self.assertContains(response, 'No shortage')
        self.assertFalse(response.context['order_has_shortage'])
        self.assertEqual(response.context['order_shortage'], Decimal('0'))
        self.assertEqual(response.context['lines_info'][0]['shortage'], Decimal('0'))

    def test_finished_goods_stock_88_order_120_shows_shortage_of_32(self):
        """with finished goods stock 88, a 120 packet order shows a shortage of 32."""
        # 50 opening + 188 produced - 150 dispatched = 88 packets
        receive_stock(
            item=self.murukku,
            qty=Decimal('188'),
            unit_cost_paise=3000,
            source_type='production',
            source_id='test_run_prod',
            user=self.admin_user,
        )
        issue_stock(
            item=self.murukku,
            qty=Decimal('150'),
            source_type='dispatch',
            source_id='test_dispatch_150',
            user=self.admin_user,
        )
        self.assertEqual(get_balance(self.murukku), Decimal('88'))

        # Create a 120 packet order for Distributor B
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.distributor_b,
            salesman=self.salesman_b,
            status=Order.STATUS_SUBMITTED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('120'),
            price_paise=5000,
        )

        # Admin opens the order detail page
        response = self.admin_client.get(reverse('admin_order_detail', args=[order.pk]))
        self.assertEqual(response.status_code, 200)

        # Shows a clear red alert / shortage of 32 packets
        self.assertContains(response, 'Shortage of 32 packets')
        self.assertTrue(response.context['order_has_shortage'])
        self.assertEqual(response.context['order_shortage'], Decimal('32'))
        self.assertEqual(response.context['lines_info'][0]['shortage'], Decimal('32'))

    def test_editing_quantity_then_approving_saves_edited_qty_and_creates_audit_log(self):
        """editing a quantity then approving saves the edited quantity and creates an audit log entry."""
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_SUBMITTED,
        )
        line = OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('150'),
            price_paise=5000,
        )

        # Step 1: Admin edits the quantity to 135 and approves the order
        post_data = {
            'action': 'approve',
            f'line_{line.id}_qty': '135',
            f'line_{line.id}_price_paise': '5000',
        }
        res = self.admin_client.post(reverse('admin_order_detail', args=[order.pk]), post_data)
        self.assertEqual(res.status_code, 302)

        # Verify edited quantity is saved
        line.refresh_from_db()
        self.assertEqual(line.qty, Decimal('135'))

        # Verify order is approved
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_APPROVED)
        self.assertTrue(hasattr(order, 'has_shortage'))

        # Verify AuditLog entries exist for the order
        logs = AuditLog.objects.filter(object_id=str(order.pk))
        self.assertTrue(logs.exists())
        self.assertTrue(logs.filter(action='edit').exists())
        self.assertTrue(logs.filter(action='approve').exists())

    def test_admin_home_review_orders_link(self):
        """Connect the 'Review Orders' card on the admin home page to the Pending Orders list."""
        response = self.admin_client.get(reverse('admin_home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn(reverse('admin_pending_orders'), content)
        self.assertIn("Review Orders", content)

    def test_reject_order_with_reason_creates_audit_log(self):
        """Rejecting an order with a reason updates status to rejected and creates an audit log entry."""
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_SUBMITTED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('50'),
            price_paise=5000,
        )

        post_data = {
            'action': 'reject',
            'rejection_reason': 'Retailer store temporarily closed',
        }
        res = self.admin_client.post(reverse('admin_order_detail', args=[order.pk]), post_data)
        self.assertEqual(res.status_code, 302)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_REJECTED)
        self.assertEqual(order.rejection_reason, 'Retailer store temporarily closed')

        log = AuditLog.objects.filter(object_id=str(order.pk), action='reject').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.details.get('status'), 'rejected')
