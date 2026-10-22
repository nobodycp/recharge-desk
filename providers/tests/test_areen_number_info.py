import json
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone

from providers.adapters.areen.client import AreenNotFound, AreenPortal
from providers.adapters.areen.number_info import (
    build_number_info,
    fields_from_parts,
    main_window,
    parse_quantity,
)
from providers.contracts import Capability
from providers.adapters.areen.adapter import AreenAdapter

NAMES = {
    "edtCelNum": "O438",
    "btnFind": "O43C",
    "btnCstmrDLYButton": "O44C",
    "lblStatus": "O4B4",
    "lblSupplier": "O48C",
    "lblMsar": "O4A0",
    "lblSimNum": "O4DC",
    "lblRemainingDays": "O504",
    "lblCostMonthly": "O52C",
}

SEARCH = (
    'O48C.setText("CELLCOM");'
    'O4A0.setText("  400GB سلكوم   ب 44 شيكل (بدون )");'
    'O4B4.setText("موصــــــول");'
    'O4DC.setText("899720201505877090");'
    'O504.setText("30");'
    'O52C.setText("44");'
)
CUSTOMER = (
    'OEA5=new Ext.window.Window({title:"عرض بيانات الزبون"});'
    "OEA5.rootObj=true;"
    'OF0F=new Ext.button.Button({id:"OF0F_id",text:"عرض تفاصيل الاشتراك",width:1});'
)
USAGE = (
    'OFE4=new Ext.window.Window({title:"تفاصيل الاشتراك"});OFE4.rootObj=true;'
    'O106F=new Ext.form.Label({id:"O106F_id",rtl:true,text:"400GB سلكوم ب 44 شيكل (بدون )",style:"a"});'
    'O106F.nm="O106F";_cdo_("edtAccProgram",O106F,null,frm);'
    'O1027=new Ext.form.Label({text:"موصــــــول"});O1027.nm="O1027";_cdo_("edtMobStatusDesc",O1027);'
    'O1057=new Ext.form.Label({text:"899720201505877090"});O1057.nm="O1057";_cdo_("edtSIM",O1057);'
    'url:"/areenweb.dll/HandleEvent?IsEvent=1&Obj=O10B2&Evt=data"'
)
ROWS = json.dumps(
    {"rows": [{"0": "رزمة انترنت M ", "1": "0.145 من 400", "2": 0}]}
)


class _Response:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def json(self):
        return json.loads(self.text)


class _Http:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = []
        self.headers = {}

    def post(self, url, data=None, headers=None, timeout=None):
        body = data.decode() if isinstance(data, bytes) else (data or "")
        self.calls.append((url, body))
        return _Response(self.bodies.pop(0))


class AreenNumberInfoParseTests(SimpleTestCase):
    def test_quantity_is_used_out_of_the_bundle(self):
        self.assertEqual(parse_quantity("0.145 من 400"), (Decimal("0.145"), Decimal("400")))
        self.assertEqual(parse_quantity("44"), (None, None))

    def test_end_date_is_today_plus_the_remaining_days(self):
        fields = fields_from_parts(NAMES, SEARCH, USAGE, json.loads(ROWS)["rows"])
        info = build_number_info(fields, date(2026, 10, 22))
        self.assertEqual(info.network, "CELLCOM")
        self.assertEqual(info.status, "موصــــــول")
        self.assertEqual(info.plan_name, "400GB سلكوم ب 44 شيكل (بدون )")
        self.assertEqual(info.plan_price, Decimal("44"))
        self.assertEqual(info.iccid, "899720201505877090")
        self.assertEqual(info.data_used_gb, Decimal("0.145"))
        self.assertEqual(info.data_total_gb, Decimal("400"))
        self.assertEqual(info.minutes_total, 0)
        self.assertEqual(info.sms_total, 0)
        self.assertEqual(timezone.localtime(info.ends_at).date(), date(2026, 11, 21))

    def test_zero_remaining_days_end_thirty_days_after_the_last_charge(self):
        search = (
            'O4B4.setText("موقوف مؤقـت تأجير ");'
            'O4A0.setText("400GB سلكوم ب 44 شيكل");'
            'O4F0.setText("19/04/2026 - 44.00 ش");'
            'O504.setText("0");'
            'O52C.setText("44");'
        )
        fields = fields_from_parts(NAMES, search, "", [])
        info = build_number_info(fields, date(2026, 10, 23))
        self.assertEqual(timezone.localtime(info.ends_at).date(), date(2026, 5, 19))
        self.assertEqual(info.status, "موقوف مؤقـت تأجير")
        self.assertEqual(info.data_total_gb, Decimal(0))
        self.assertIsNone(info.data_used_gb)

    def test_we_window_uses_remaining_gigabytes_and_the_subscription_grid(self):
        search = (
            'O48C.setText("WECOM");'
            'O4A0.setText("500 جيغا وي ب 32 شيكل (بدون عموله)");'
            'O504.setText("20");'
            'O52C.setText("32");'
            'O4DC.setText("89972096691000396155");'
        )
        usage = (
            'O29E8=new Ext.form.Label({text:"500"});'
            'O29E8.nm="O29E8";_cdo_("lblNetDataPKG",O29E8);'
            'O29B8=new Ext.form.Label({text:"متبقي493.11GB"});'
            'O29B8.nm="O29B8";_cdo_("lblDataNetRem",O29B8);'
            'O29DC=new Ext.form.Label({text:"0"});'
            'O29DC.nm="O29DC";_cdo_("lblCallMinute",O29DC);'
            'O29D0=new Ext.form.Label({text:"1"});'
            'O29D0.nm="O29D0";_cdo_("lblSMS",O29D0);'
            'O1=new Ext.form.Label({text:"مكالمات ورسائل SMS \\nبدون تحديد"});'
            'O1.nm="O1";_cdo_("lbl14",O1);'
        )
        rows = [{"0": "0512752923", "1": "انس", "2": "موصــــــول"}]
        fields = fields_from_parts(NAMES, search, usage, [], rows)
        info = build_number_info(fields, date(2026, 10, 23))
        self.assertEqual(info.status, "موصــــــول")
        self.assertEqual(info.data_total_gb, Decimal("500"))
        self.assertEqual(info.data_used_gb, Decimal("6.89"))
        self.assertEqual(info.minutes_used, 0)
        self.assertIsNone(info.minutes_total)
        self.assertTrue(info.minutes_unlimited)
        self.assertEqual(info.sms_used, 1)
        self.assertTrue(info.sms_unlimited)
        self.assertEqual(timezone.localtime(info.ends_at).date(), date(2026, 11, 12))

    def test_main_window_is_the_one_that_owns_the_phone_field(self):
        script = (
            'O1.rootObj=true;_cdo_("frmLogin");'
            'O2A8.rootObj=true;_cdo_("edtCelNum",O438);'
        )
        self.assertEqual(main_window(script, "edtCelNum"), "O2A8")


class AreenNumberInfoRequestTests(SimpleTestCase):
    def test_password_login_opens_the_subscribe_screen(self):
        menu = (
            'O2.rootObj=true;_cdo_("frmMaster");'
            'O1EA=new Ext.button.Button({text:"اشتراك وشحن"});'
            'O1EA.nm="O1EA";_cdo_("btnMMtfaseel",O1EA);'
        )
        search = (
            'O9.rootObj=true;_cdo_("edtCelNum",O438);'
            '_cdo_("btnFind",O43C);_cdo_("btnCstmrDLYButton",O44C);'
        )
        http = _Http([search])
        opened = AreenPortal(http=http).open_subscriber_search("sid", menu)
        self.assertIn("edtCelNum", opened)
        self.assertIn("Obj=O1EA", http.calls[0][1])
        self.assertIn("Evt=click", http.calls[0][1])

    def test_an_open_search_screen_is_left_as_it_is(self):
        search = 'O9.rootObj=true;_cdo_("edtCelNum",O438);'
        http = _Http([])
        opened = AreenPortal(http=http).open_subscriber_search("sid", search)
        self.assertEqual(opened, search)
        self.assertEqual(http.calls, [])

    def test_lookup_reads_the_details_window_and_the_usage_row(self):
        http = _Http(["", "", SEARCH, CUSTOMER, USAGE, ROWS])
        session = {
            "session_id": "sid",
            "window": "O2A8",
            "seq": 10,
            "names": NAMES,
        }
        fields = AreenPortal(http=http).read_subscription(session, "0523875866")
        info = build_number_info(fields, date(2026, 10, 22))
        self.assertEqual(info.plan_name, "400GB سلكوم ب 44 شيكل (بدون )")
        self.assertEqual(info.data_used_gb, Decimal("0.145"))
        self.assertGreater(session["seq"], 10)
        change = http.calls[1][1]
        self.assertIn("Obj=O438", change)
        self.assertIn("Evt=change", change)
        self.assertIn("newValue=0523875866", change)
        self.assertIn("newValue=", http.calls[0][1])
        self.assertIn("Evt=data", http.calls[-1][0])

    def test_a_missing_number_stops_before_the_details_window(self):
        http = _Http(["", "", 'toastr.error("الزبون / الاشتراك غير موجود!");'])
        session = {"session_id": "sid", "window": "O2A8", "seq": 1, "names": NAMES}
        with self.assertRaises(AreenNotFound):
            AreenPortal(http=http).read_subscription(session, "0500000000")
        self.assertEqual(len(http.calls), 3)

    def test_number_check_is_a_portal_capability_and_refresh_is_not(self):
        self.assertIn(Capability.NUMBER_INFO, AreenAdapter.capabilities)
        self.assertEqual(AreenAdapter.login_capabilities, frozenset({Capability.NUMBER_INFO}))
