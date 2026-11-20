from datetime import date, timedelta
import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.db.models import Sum
from django.test import Client, TestCase
from django.urls import reverse

from core.ledger import InsufficientStock, get_balance, issue_stock, receive_stock
from core.models import AuditLog, Customer, Item, StockLot, StockMovement
from sales.models import CreditNote, Dispatch, Invoice, InvoiceLine, Order, OrderLine, Payment
from sales.services import (
    calculate_tax,
    cancel_invoice,
    create_invoice,
    customer_outstanding,
    dispatch_invoice,
    next_invoice_number,
    record_payment,
)

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


class SalesInvoiceBillingTests(TestCase):
    def setUp(self):
        call_command('seed_benchmark', verbosity=0)
        self.admin_user = User.objects.get(username='admin1')
        self.salesman_a = User.objects.get(username='salesman_a')
        self.salesman_b = User.objects.get(username='salesman_b')
        self.retailer_a = Customer.objects.get(name='Retailer A')
        self.distributor_b = Customer.objects.get(name='Distributor B')
        self.murukku = Item.objects.get(name='Murukku 200 g')

        self.client_a = Client()
        self.client_a.login(username='salesman_a', password='demo1234')

        self.admin_client = Client()
        self.admin_client.login(username='admin1', password='demo1234')

        self.owner_user = User.objects.get(username='owner1')
        self.owner_client = Client()
        self.owner_client.login(username='owner1', password='demo1234')

    def test_tax_calculation_intra_state_retailer_a(self):
        """Retailer A, 150 packets at 5000 paise gives taxable 750000, CGST 18750, SGST 18750, total 787500"""
        taxable_paise = 150 * 5000  # 750000
        tax = calculate_tax(
            customer_state=self.retailer_a.state,
            taxable_paise=taxable_paise,
            gst_rate=self.murukku.gst_rate_percent,
        )
        self.assertEqual(tax.taxable_paise, 750000)
        self.assertEqual(tax.cgst_paise, 18750)
        self.assertEqual(tax.sgst_paise, 18750)
        self.assertEqual(tax.igst_paise, 0)
        self.assertEqual(tax.total_paise, 787500)

    def test_tax_calculation_inter_state_distributor_b(self):
        """Distributor B (Karnataka) on the same amount gives IGST 37500 and CGST/SGST 0"""
        taxable_paise = 150 * 5000  # 750000
        tax = calculate_tax(
            customer_state=self.distributor_b.state,
            taxable_paise=taxable_paise,
            gst_rate=self.murukku.gst_rate_percent,
        )
        self.assertEqual(tax.taxable_paise, 750000)
        self.assertEqual(tax.cgst_paise, 0)
        self.assertEqual(tax.sgst_paise, 0)
        self.assertEqual(tax.igst_paise, 37500)
        self.assertEqual(tax.total_paise, 787500)

    def test_invoice_numbers_are_sequential_with_no_gaps(self):
        """invoice numbers are sequential with no gaps and sequence resets each year"""
        n1 = next_invoice_number(date(2026, 4, 1))
        self.assertEqual(n1, '2026-27/0001')

        n2 = next_invoice_number(date(2026, 5, 10))
        self.assertEqual(n2, '2026-27/0002')

        n3 = next_invoice_number(date(2027, 3, 31))
        self.assertEqual(n3, '2026-27/0003')

        # New financial year resets sequence to 0001
        n4 = next_invoice_number(date(2027, 4, 1))
        self.assertEqual(n4, '2027-28/0001')

        n5 = next_invoice_number(date(2027, 4, 2))
        self.assertEqual(n5, '2027-28/0002')

    def test_full_invoice_dispatch_payment_cancel_benchmark_scenario(self):
        """
        Benchmark scenario test:
        - Finished goods stock goes from 238 to 88 after dispatch
        - The opening lot (earlier expiry) is consumed first
        - Payment of 400000 leaves outstanding 387500
        - Cancelling an invoice restores stock to 238 and the invoice row still exists
        """
        # Benchmark opening stock has 50 packets with expiry today + 60 days
        opening_lot = StockLot.objects.get(item=self.murukku, source_type='opening')
        self.assertEqual(get_balance(self.murukku), Decimal('50'))

        # Add production run lot of 188 packets with expiry today + 90 days
        prod_movement = receive_stock(
            item=self.murukku,
            qty=Decimal('188'),
            unit_cost_paise=3000,
            source_type='production',
            source_id='run-1',
            user=self.admin_user,
            batch_no='MUR-20261120-1',
            expiry_date=date.today() + timedelta(days=90),
        )
        prod_lot = prod_movement.lot

        # Total finished goods stock is now 50 + 188 = 238
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # Salesman A orders 150 packets for Retailer A
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

        # Admin approves the order
        order.status = Order.STATUS_APPROVED
        order.save()

        # Admin raises the GST invoice
        invoice = create_invoice(order, user=self.admin_user)
        self.assertEqual(invoice.status, Invoice.STATUS_POSTED)
        self.assertEqual(invoice.taxable_paise, 750000)
        self.assertEqual(invoice.cgst_paise, 18750)
        self.assertEqual(invoice.sgst_paise, 18750)
        self.assertEqual(invoice.igst_paise, 0)
        self.assertEqual(invoice.total_paise, 787500)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_INVOICED)

        # Invoice lines created
        inv_line = invoice.lines.first()
        self.assertIsNotNone(inv_line)
        self.assertEqual(inv_line.qty, Decimal('150'))
        self.assertEqual(inv_line.taxable_paise, 750000)
        self.assertIsNone(inv_line.lot)

        # Dispatch the invoice
        dispatch = dispatch_invoice(invoice, user=self.admin_user)
        self.assertIsNotNone(dispatch)
        self.assertEqual(dispatch.status, Dispatch.STATUS_DISPATCHED)

        # Check finished goods stock goes from 238 to 88
        self.assertEqual(get_balance(self.murukku), Decimal('88'))

        # Check the opening lot (earlier expiry) is consumed first
        opening_lot_balance = StockMovement.objects.filter(lot=opening_lot).aggregate(
            total=Sum('qty')
        )['total']
        self.assertEqual(opening_lot_balance, Decimal('0'))

        # The rest (100 packets) came from the newer lot
        prod_lot_balance = StockMovement.objects.filter(lot=prod_lot).aggregate(
            total=Sum('qty')
        )['total']
        self.assertEqual(prod_lot_balance, Decimal('88'))

        # Line lot was saved
        inv_line.refresh_from_db()
        self.assertEqual(inv_line.lot, opening_lot)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_DISPATCHED)

        # Payment of 400000 leaves outstanding 387500
        payment = record_payment(
            invoice=invoice,
            amount_paise=400000,
            mode='bank_transfer',
            reference='TXN123456',
            user=self.admin_user,
        )
        self.assertIsNotNone(payment)
        self.assertEqual(customer_outstanding(self.retailer_a), 387500)

        # Cancelling an invoice restores stock to 238 and the invoice row still exists
        credit_note = cancel_invoice(
            invoice=invoice,
            reason='Retailer requested order cancellation',
            user=self.admin_user,
        )
        self.assertIsNotNone(credit_note)
        self.assertEqual(credit_note.total_paise, 787500)

        # Stock restored to 238
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # Invoice row still exists with status cancelled
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_CANCELLED)
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_dispatching_with_not_enough_stock_raises_insufficient_stock_and_changes_nothing(self):
        """dispatching with not enough stock raises InsufficientStock and changes nothing"""
        # Murukku opening stock is 50 packets
        self.assertEqual(get_balance(self.murukku), Decimal('50'))

        # Order 120 packets (shortage of 70 packets)
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.distributor_b,
            salesman=self.salesman_b,
            status=Order.STATUS_APPROVED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('120'),
            price_paise=5000,
        )

        invoice = create_invoice(order, user=self.admin_user)
        self.assertEqual(invoice.status, Invoice.STATUS_POSTED)
        self.assertEqual(order.status, Order.STATUS_INVOICED)

        # Attempt to dispatch must raise InsufficientStock
        with self.assertRaises(InsufficientStock):
            dispatch_invoice(invoice, user=self.admin_user)

        # Stock must remain unchanged at 50
        self.assertEqual(get_balance(self.murukku), Decimal('50'))

        # No dispatch record created
        self.assertEqual(Dispatch.objects.filter(invoice=invoice).count(), 0)

        # Order status remains invoiced
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_INVOICED)

        # Invoice line lot remains None
        line = invoice.lines.first()
        self.assertIsNone(line.lot)

    def test_create_invoice_fails_if_order_not_approved(self):
        """create_invoice only works for orders with status approved"""
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_SUBMITTED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('10'),
            price_paise=5000,
        )

        with self.assertRaises(ValidationError):
            create_invoice(order, user=self.admin_user)

    def test_create_invoice_from_approved_150_packet_order_shows_7875(self):
        """create an invoice from an approved 150 packet order and the page shows ₹7,875.00"""
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_APPROVED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('150'),
            price_paise=5000,
        )

        # Admin order detail page shows "Create Invoice" button for approved order
        response_order = self.admin_client.get(reverse('admin_order_detail', args=[order.pk]))
        self.assertEqual(response_order.status_code, 200)
        self.assertContains(response_order, "Create Invoice")

        # Create invoice via POST
        create_resp = self.admin_client.post(reverse('create_invoice', args=[order.pk]), follow=True)
        self.assertEqual(create_resp.status_code, 200)

        invoice = Invoice.objects.filter(order=order).first()
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.total_paise, 787500)

        # Invoice page shows ₹7,875.00 and all required elements
        inv_resp = self.admin_client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertEqual(inv_resp.status_code, 200)
        self.assertContains(inv_resp, "₹7,875.00")
        self.assertContains(inv_resp, invoice.number)
        self.assertContains(inv_resp, "33AAAAA0000A1Z5")  # Retailer A GSTIN
        self.assertContains(inv_resp, "Download PDF")
        self.assertContains(inv_resp, "Dispatch")
        self.assertContains(inv_resp, "Cancel Invoice")
        self.assertContains(inv_resp, "wa.me")  # WhatsApp share link

    def test_invoice_pdf_download_returns_status_200_with_pdf_content_type(self):
        """the PDF download returns status 200 with content type application/pdf"""
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_APPROVED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('150'),
            price_paise=5000,
        )
        invoice = create_invoice(order, user=self.admin_user)

        pdf_resp = self.admin_client.get(reverse('invoice_pdf', args=[invoice.pk]))
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp['Content-Type'], 'application/pdf')
        self.assertTrue(pdf_resp.content.startswith(b'%PDF'))

    def test_dispatch_reduces_finished_goods_stock_from_238_to_88(self):
        """dispatch reduces finished goods stock from 238 to 88"""
        # Starting murukku stock is 50; receive 188 to bring total to 238
        receive_stock(
            item=self.murukku,
            qty=Decimal('188'),
            unit_cost_paise=3000,
            source_type='production',
            source_id='run_1',
            user=self.admin_user,
        )
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # Create approved order for 150 packets and invoice it
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_APPROVED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('150'),
            price_paise=5000,
        )
        invoice = create_invoice(order, user=self.admin_user)

        # Dispatch via POST to invoice_dispatch
        dispatch_resp = self.admin_client.post(reverse('invoice_dispatch', args=[invoice.pk]), follow=True)
        self.assertEqual(dispatch_resp.status_code, 200)

        # Stock is reduced from 238 to 88
        self.assertEqual(get_balance(self.murukku), Decimal('88'))

        # Verify dispatch record created and order dispatched
        self.assertTrue(invoice.is_dispatched)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_DISPATCHED)

    def test_salesman_a_gets_403_on_invoices_list(self):
        """salesman_a gets 403 on the invoices list"""
        # Salesman A gets 403 on invoices list
        resp = self.client_a.get(reverse('invoice_list'))
        self.assertEqual(resp.status_code, 403)

        # Admin and owner get 200 on invoices list
        admin_resp = self.admin_client.get(reverse('invoice_list'))
        self.assertEqual(admin_resp.status_code, 200)

        owner_resp = self.owner_client.get(reverse('invoice_list'))
        self.assertEqual(owner_resp.status_code, 200)

    def test_dispatch_with_insufficient_stock_shows_red_error_and_changes_nothing(self):
        """dispatch button calls dispatch_invoice; show a clear red message if stock is not enough and change nothing"""
        # Stock is 50
        self.assertEqual(get_balance(self.murukku), Decimal('50'))

        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.distributor_b,
            salesman=self.salesman_b,
            status=Order.STATUS_APPROVED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('120'),
            price_paise=5000,
        )
        invoice = create_invoice(order, user=self.admin_user)

        # Admin attempts to dispatch
        resp = self.admin_client.post(reverse('invoice_dispatch', args=[invoice.pk]), follow=True)
        self.assertEqual(resp.status_code, 200)

        # Stock remains unchanged at 50
        self.assertEqual(get_balance(self.murukku), Decimal('50'))

        # No dispatch record created
        self.assertFalse(invoice.is_dispatched)

        # Error message shown on page (red message in alert)
        self.assertContains(resp, "Insufficient stock")
        self.assertContains(resp, "bg-rose-50")

    def test_record_payment_invoice_status_cancel_outstanding_and_permissions(self):
        """
        Tests: payment of 400000 paise on the 787500 paise invoice leaves outstanding 387500 and shows "part paid";
        paying the balance shows "paid"; cancelling a dispatched invoice restores finished goods stock to 238
        and keeps the invoice row; salesman_a gets 403 on the payment page.
        """
        # Create accountant client
        acct_client = Client()
        acct_client.login(username='acct1', password='demo1234')

        # 1. salesman_a gets 403 on the payment page
        resp_salesman = self.client_a.get(reverse('record_payment'))
        self.assertEqual(resp_salesman.status_code, 403)
        resp_salesman_post = self.client_a.post(reverse('record_payment'), {'customer': self.retailer_a.pk})
        self.assertEqual(resp_salesman_post.status_code, 403)

        # Accountant gets 403 on payment page
        resp_acct_pay = acct_client.get(reverse('record_payment'))
        self.assertEqual(resp_acct_pay.status_code, 403)

        # Admin and owner get 200 on payment page
        self.assertEqual(self.admin_client.get(reverse('record_payment')).status_code, 200)
        self.assertEqual(self.owner_client.get(reverse('record_payment')).status_code, 200)

        # Setup 238 packets stock: 50 opening + 188 production
        receive_stock(
            item=self.murukku,
            qty=Decimal('188'),
            unit_cost_paise=3000,
            source_type='production',
            source_id='run-p1',
            user=self.admin_user,
            batch_no='MUR-TEST-P1',
            expiry_date=date.today() + timedelta(days=90),
        )
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # Order 150 packets for Retailer A
        order = Order.objects.create(
            client_uuid=uuid.uuid4(),
            customer=self.retailer_a,
            salesman=self.salesman_a,
            status=Order.STATUS_APPROVED,
        )
        OrderLine.objects.create(
            order=order,
            item=self.murukku,
            qty=Decimal('150'),
            price_paise=5000,
        )
        invoice = create_invoice(order, user=self.admin_user)
        self.assertEqual(invoice.total_paise, 787500)

        # Dispatch invoice -> stock drops to 88
        dispatch_invoice(invoice, user=self.admin_user)
        self.assertEqual(get_balance(self.murukku), Decimal('88'))

        # Initially invoice shows unpaid
        inv_detail_resp = self.admin_client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertEqual(inv_detail_resp.status_code, 200)
        self.assertContains(inv_detail_resp, "unpaid")

        # Payment of 400000 paise on the 787500 paise invoice leaves outstanding 387500 and shows "part paid"
        pay_resp = self.admin_client.post(reverse('record_payment'), {
            'invoice': invoice.pk,
            'customer': self.retailer_a.pk,
            'amount_paise': 400000,
            'date': date.today().isoformat(),
            'mode': 'bank',
            'reference': 'TXN400000',
        }, follow=True)
        self.assertEqual(pay_resp.status_code, 200)

        self.assertEqual(customer_outstanding(self.retailer_a), 387500)

        inv_detail_resp2 = self.admin_client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertContains(inv_detail_resp2, "part paid")
        self.assertContains(inv_detail_resp2, "3,875")

        # Verify audit log entry for payment
        self.assertTrue(AuditLog.objects.filter(action='record_payment', object_id=str(invoice.payments.first().pk)).exists())

        # Paying the balance shows "paid"
        pay_bal_resp = self.admin_client.post(reverse('record_payment'), {
            'invoice': invoice.pk,
            'customer': self.retailer_a.pk,
            'amount_paise': 387500,
            'date': date.today().isoformat(),
            'mode': 'upi',
            'reference': 'UPI387500',
        }, follow=True)
        self.assertEqual(pay_bal_resp.status_code, 200)

        self.assertEqual(customer_outstanding(self.retailer_a), 0)

        inv_detail_resp3 = self.admin_client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertContains(inv_detail_resp3, "paid")

        # Test Customer Outstanding page permissions and contents
        # Salesman A gets 403 on outstanding page
        self.assertEqual(self.client_a.get(reverse('customer_outstanding')).status_code, 403)
        # Accountant gets 200 (read-only)
        acct_out_resp = acct_client.get(reverse('customer_outstanding'))
        self.assertEqual(acct_out_resp.status_code, 200)
        # Check formatting like ₹3,875.00 or ₹7,875.00
        self.assertContains(acct_out_resp, "₹7,875.00")
        # Check ageing bucket headers
        self.assertContains(acct_out_resp, "0-30 Days")
        self.assertContains(acct_out_resp, "31-60 Days")
        self.assertContains(acct_out_resp, "61-90 Days")
        self.assertContains(acct_out_resp, "90+ Days")

        # Cancelling a dispatched invoice restores finished goods stock to 238 and keeps the invoice row
        cancel_resp = self.admin_client.post(reverse('invoice_cancel', args=[invoice.pk]), {
            'reason': 'Quality dispute by customer',
        }, follow=True)
        self.assertEqual(cancel_resp.status_code, 200)

        # Restores finished goods stock to 238
        self.assertEqual(get_balance(self.murukku), Decimal('238'))

        # Keeps invoice row with status cancelled
        invoice.refresh_from_db()
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())
        self.assertEqual(invoice.status, Invoice.STATUS_CANCELLED)

        # Shows grey "Cancelled" badge and credit note number
        inv_cancel_page = self.admin_client.get(reverse('invoice_detail', args=[invoice.pk]))
        self.assertContains(inv_cancel_page, "Cancelled")
        self.assertContains(inv_cancel_page, invoice.credit_notes.first().number)

        # Verify audit log entry for cancellation
        self.assertTrue(AuditLog.objects.filter(action='cancel_invoice', object_id=str(invoice.pk)).exists())

