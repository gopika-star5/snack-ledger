from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group

class Command(BaseCommand):
    help = "Creates roles (groups) and demo users for the factory system"

    def handle(self, *args, **options):
        # 1. Define required groups
        group_names = ['salesman', 'admin', 'production', 'accountant', 'owner']
        groups = {}
        for name in group_names:
            group, created = Group.objects.get_or_create(name=name)
            groups[name] = group
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created group: '{name}'"))
            else:
                self.stdout.write(f"Group already exists: '{name}'")

        # 2. Define demo users and their respective roles
        demo_users = [
            ('salesman_a', 'salesman'),
            ('salesman_b', 'salesman'),
            ('admin1', 'admin'),
            ('prod1', 'production'),
            ('acct1', 'accountant'),
            ('owner1', 'owner'),
        ]

        password = 'demo1234'
        for username, role_name in demo_users:
            user, created = User.objects.get_or_create(username=username)
            user.set_password(password)
            if role_name == 'admin':
                user.is_staff = True
            user.save()

            # Ensure user has only the designated demo role group
            user.groups.clear()
            user.groups.add(groups[role_name])

            status = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{status} user '{username}' with role '{role_name}'"))

        self.stdout.write(self.style.SUCCESS("All demo users and groups created successfully."))
