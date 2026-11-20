from django.core.management.base import BaseCommand
from core.benchmark import run_benchmark


class Command(BaseCommand):
    help = "Run the Section 5 benchmark scenario inside a rolled-back transaction and print results"

    def handle(self, *args, **options):
        results = run_benchmark()

        col_check = max(len("Check"), max(len(r['check']) for r in results))
        col_exp = max(len("Expected"), max(len(r['expected']) for r in results))
        col_act = max(len("Actual"), max(len(r['actual']) for r in results))

        header = f"{'Check'.ljust(col_check)} | {'Expected'.ljust(col_exp)} | {'Actual'.ljust(col_act)} | Status"
        separator = "-" * len(header)

        self.stdout.write("")
        self.stdout.write(header)
        self.stdout.write(separator)

        all_passed = True
        for r in results:
            status = r['status']
            if status != 'PASS':
                all_passed = False
            line = f"{r['check'].ljust(col_check)} | {r['expected'].ljust(col_exp)} | {r['actual'].ljust(col_act)} | {status}"
            if status == 'PASS':
                self.stdout.write(self.style.SUCCESS(line))
            else:
                self.stdout.write(self.style.ERROR(line))

        self.stdout.write(separator)
        if all_passed:
            self.stdout.write(self.style.SUCCESS(f"All {len(results)} benchmark checks PASSED.\n"))
        else:
            self.stdout.write(self.style.ERROR("Some benchmark checks FAILED.\n"))
