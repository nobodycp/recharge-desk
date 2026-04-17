from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import UserProfile
from companies.models import Company, Product, ProductLine
from sales.models import PaymentMethod
from sales.services import initialize_company_opening_balance

User = get_user_model()


class Command(BaseCommand):
    help = "Create demo users, suppliers, products, and payment methods (safe to re-run)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding demo data…"))

        for name in ["Bank of Palestine", "PalPay Wallet", "Jawwal Pay Wallet"]:
            PaymentMethod.objects.get_or_create(name=name, defaults={"is_active": True})

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_superuser": True, "is_staff": True, "is_active": True},
        )
        admin.is_superuser = True
        admin.is_staff = True
        admin.is_active = True
        admin.set_password("admin1234")
        admin.save()
        prof, _ = UserProfile.objects.get_or_create(user=admin)
        prof.full_name = "Administrator"
        prof.role = UserProfile.Role.MANAGEMENT
        prof.is_active_profile = True
        prof.save()

        emp, _ = User.objects.get_or_create(username="employee1", defaults={"is_active": True})
        emp.is_active = True
        emp.set_password("employee1234")
        emp.save()
        eprof, _ = UserProfile.objects.get_or_create(user=emp)
        eprof.full_name = "Employee One"
        eprof.role = UserProfile.Role.EMPLOYEE
        eprof.is_active_profile = True
        eprof.save()

        layan, created = Company.objects.get_or_create(
            name="Layan",
            defaults={
                "opening_balance": Decimal("5000"),
                "current_balance": Decimal("0"),
                "is_active": True,
            },
        )
        if created:
            initialize_company_opening_balance(company=layan, user=admin)

        sky, created = Company.objects.get_or_create(
            name="Sky",
            defaults={
                "opening_balance": Decimal("0"),
                "current_balance": Decimal("0"),
                "is_active": True,
            },
        )
        if created:
            initialize_company_opening_balance(company=sky, user=admin)

        # Product lines + packages (variants)
        lines_and_packages = [
            (layan, "Weccom", [("", Decimal("30"), Decimal("50"))]),
            (layan, "Hot Mobile", [("", Decimal("35"), Decimal("60"))]),
            (layan, "Partner", [("", Decimal("39"), Decimal("70"))]),
            (sky, "Cellcom", [("", Decimal("45"), Decimal("70"))]),
            (sky, "Pelephone500", [("", Decimal("55"), Decimal("90"))]),
        ]
        for company, line_name, packages in lines_and_packages:
            line, _ = ProductLine.objects.get_or_create(
                company=company,
                name=line_name,
                defaults={"sort_order": 0, "is_active": True},
            )
            for label, cost, sell in packages:
                Product.objects.get_or_create(
                    line=line,
                    variant_label=label,
                    defaults={
                        "cost_price": cost,
                        "default_sell_price": sell,
                        "is_active": True,
                    },
                )

        self.stdout.write(self.style.SUCCESS("Done. Users: admin / admin1234, employee1 / employee1234"))
