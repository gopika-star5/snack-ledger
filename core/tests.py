from decimal import Decimal
from django.test import TestCase, Client
from django.core.management import call_command
from django.contrib.auth.models import User, Group
from django.urls import reverse


class DemoUsersCommandTests(TestCase):
    """Test management command create_demo_users."""

    def test_create_demo_users(self):
        call_command('create_demo_users')

        expected_roles = ['salesman', 'admin', 'production', 'accountant', 'owner']
        for role in expected_roles:
            self.assertTrue(Group.objects.filter(name=role).exists(), f"Group {role} should exist.")

        expected_users = {
            'salesman_a': 'salesman',
            'salesman_b': 'salesman',
            'admin1': 'admin',
            'prod1': 'production',
            'acct1': 'accountant',
            'owner1': 'owner',
        }

        for username, role in expected_users.items():
            user = User.objects.filter(username=username).first()
            self.assertIsNotNone(user, f"User {username} should exist.")
            self.assertTrue(user.check_password('demo1234'), f"User {username} password should be demo1234.")
            self.assertTrue(user.groups.filter(name=role).exists(), f"User {username} should belong to {role}.")

    def test_idempotent_command(self):
        call_command('create_demo_users')
        # Running a second time should not raise errors
        call_command('create_demo_users')
        self.assertEqual(User.objects.filter(username='salesman_a').count(), 1)


class RoleBasedAccessTests(TestCase):
    """Test role-based access control and 403 blocking."""

    def setUp(self):
        call_command('create_demo_users')
        self.client = Client()

        self.home_urls = {
            'salesman': reverse('salesman_home'),
            'admin': reverse('admin_home'),
            'production': reverse('production_home'),
            'accountant': reverse('accountant_home'),
            'owner': reverse('owner_home'),
        }

        self.users = {
            'salesman_a': 'salesman',
            'salesman_b': 'salesman',
            'admin1': 'admin',
            'prod1': 'production',
            'acct1': 'accountant',
            'owner1': 'owner',
        }

    def test_each_role_can_open_own_home_and_gets_403_on_others(self):
        """
        Verify that:
        - Each role can open its own home page (200 OK)
        - Each role receives a 403 Forbidden on another role's home page
        """
        for username, user_role in self.users.items():
            self.client.login(username=username, password='demo1234')

            for target_role, url in self.home_urls.items():
                response = self.client.get(url)
                if target_role == user_role:
                    self.assertEqual(
                        response.status_code, 200,
                        f"User '{username}' with role '{user_role}' should access '{target_role}' home at {url} (expected 200, got {response.status_code})."
                    )
                else:
                    self.assertEqual(
                        response.status_code, 403,
                        f"User '{username}' with role '{user_role}' should be blocked from '{target_role}' home at {url} with 403 (got {response.status_code})."
                    )

            self.client.logout()

    def test_login_redirect_by_role(self):
        """Test that logging in via login view redirects user directly to their role home page."""
        for username, role in self.users.items():
            response = self.client.post(reverse('login'), {
                'username': username,
                'password': 'demo1234',
            })
            expected_url = self.home_urls[role]
            self.assertRedirects(response, expected_url, fetch_redirect_response=False)
            self.client.logout()

    def test_root_redirect_for_authenticated_users(self):
        """Test that accessing '/' redirects authenticated user to their role home page."""
        for username, role in self.users.items():
            self.client.login(username=username, password='demo1234')
            response = self.client.get('/')
            expected_url = self.home_urls[role]
            self.assertRedirects(response, expected_url, fetch_redirect_response=False)
            self.client.logout()

    def test_unauthenticated_access_redirects_to_login(self):
        """Unauthenticated requests to any role home page should redirect to login."""
        for role, url in self.home_urls.items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('login')))

    def test_logout(self):
        """Test logout functionality."""
        self.client.login(username='salesman_a', password='demo1234')
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'))
        # Should now be unauthenticated
        res = self.client.get(self.home_urls['salesman'])
        self.assertEqual(res.status_code, 302)


# ---------------------------------------------------------------------------
# Unit conversion tests
# ---------------------------------------------------------------------------

class UnitConvertTests(TestCase):
    """Tests for core.units.convert()."""

    # Data migration 0002 is NOT run in test suite by default for TestCase
    # (Django rolls back after each test class and re-applies migrations fresh).
    # We seed the units we need directly in setUp.
    def setUp(self):
        from core.models import Unit
        self.kg     = Unit.objects.get_or_create(code='kg',     defaults={'kind': 'mass',   'factor_to_base': Decimal('1000')})[0]
        self.g      = Unit.objects.get_or_create(code='g',      defaults={'kind': 'mass',   'factor_to_base': Decimal('1')})[0]
        self.L      = Unit.objects.get_or_create(code='L',      defaults={'kind': 'volume', 'factor_to_base': Decimal('1000')})[0]
        self.ml     = Unit.objects.get_or_create(code='ml',     defaults={'kind': 'volume', 'factor_to_base': Decimal('1')})[0]
        self.piece  = Unit.objects.get_or_create(code='piece',  defaults={'kind': 'count',  'factor_to_base': Decimal('1')})[0]
        self.packet = Unit.objects.get_or_create(code='packet', defaults={'kind': 'count',  'factor_to_base': Decimal('1')})[0]
        self.carton = Unit.objects.get_or_create(code='carton', defaults={'kind': 'count',  'factor_to_base': Decimal('20')})[0]

    def test_kg_to_g(self):
        from core.units import convert
        result = convert(Decimal('40'), self.kg, self.g)
        self.assertEqual(result, Decimal('40000'))

    def test_L_to_ml(self):
        from core.units import convert
        result = convert(Decimal('10.5'), self.L, self.ml)
        self.assertEqual(result, Decimal('10500'))

    def test_carton_to_packet(self):
        from core.units import convert
        result = convert(Decimal('3'), self.carton, self.packet)
        self.assertEqual(result, Decimal('60'))

    def test_cross_kind_raises(self):
        from core.units import convert
        with self.assertRaises(ValueError):
            convert(Decimal('1'), self.kg, self.L)


# ---------------------------------------------------------------------------
# StockMovement immutability tests
# ---------------------------------------------------------------------------

class StockMovementImmutabilityTests(TestCase):
    """StockMovement rows must never be edited or deleted."""

    def setUp(self):
        from core.models import Unit, Item, StockMovement
        self.unit = Unit.objects.create(code='g_test', kind='mass', factor_to_base=Decimal('1'))
        self.item = Item.objects.create(
            name='Test Item',
            item_type='raw',
            stock_unit=self.unit,
            hsn_code='1234',
            gst_rate_percent=Decimal('5.00'),
        )
        self.movement = StockMovement.objects.create(
            item=self.item,
            qty=Decimal('100.000'),
            unit_cost_paise=4200,
            source_type='test',
        )

    def test_edit_raises_error(self):
        from core.models import ImmutableStockMovementError, StockMovement
        sm = StockMovement.objects.get(pk=self.movement.pk)
        sm.note = 'changed'
        with self.assertRaises(ImmutableStockMovementError):
            sm.save()

    def test_delete_instance_raises_error(self):
        from core.models import ImmutableStockMovementError, StockMovement
        sm = StockMovement.objects.get(pk=self.movement.pk)
        with self.assertRaises(ImmutableStockMovementError):
            sm.delete()

    def test_queryset_delete_raises_error(self):
        from core.models import ImmutableStockMovementError, StockMovement
        with self.assertRaises(ImmutableStockMovementError):
            StockMovement.objects.filter(pk=self.movement.pk).delete()

    def test_queryset_update_raises_error(self):
        from core.models import ImmutableStockMovementError, StockMovement
        with self.assertRaises(ImmutableStockMovementError):
            StockMovement.objects.filter(pk=self.movement.pk).update(note='changed')
