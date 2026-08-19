import unittest

from server.roadmap_asset_resolution import canonical_asset_target


class RoadmapDatasetResolutionTests(unittest.TestCase):
    def test_corporate_spend_labels_resolve_to_canonical_assets(self):
        cases = {
            "ERP/AP data (invoices, payments, GL postings)": ("ERP", "Finance & Controlling"),
            "ERP financial extract (SAP / Oracle / Workday)": ("ERP", "Finance & Controlling"),
            "Purchase orders and requisitions": ("ERP", "Procurement / Sourcing"),
            "Contracts and terms data": ("ERP", "Procurement / Sourcing"),
            "Vendor master and performance data": ("ERP", "Procurement / Sourcing"),
            "Inventory levels and turnover data": ("ERP", "Inventory / Warehouse"),
            "Termination + hire records": ("ERP", "Human Capital Management"),
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(canonical_asset_target(label), expected)

    def test_utility_operational_labels_resolve_to_canonical_assets(self):
        cases = {
            "AMI interval usage and meter read data": ("Meter/AMI/MDM", "Interval Usage Collection"),
            "AMI device registry": ("Meter/AMI/MDM", "Interval Usage Collection"),
            "GIS asset location and network model": ("GIS", "Electric Network Model / Connectivity"),
            "SCADA telemetry and historian tags": ("Standalone SCADA", "Real-Time Telemetry & Control"),
            "Program enrollment history (DR, EE, budget billing)": ("CIS/Billing", "Programs / DSM Enrollment"),
            "Outage restoration and trouble call data": ("ADMS (Distribution)", "OMS (Outage Management)"),
            "Cathodic protection readings (test stations, rectifiers)": ("EAM/APM", "Asset Registry & Hierarchy"),
            "Customer data warehouse with unified customer ID": ("CIS/Billing", "Customer Accounts"),
            "Daily line MW peak / average exports from EMS": ("EMS (Transmission)", "T-SCADA"),
            "Injection source hydrogen availability": ("Fuel Management", "Fuel Quality / Blending"),
            "Customer complaint records": ("CIS/Billing", "CRM / Contact Center"),
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(canonical_asset_target(label), expected)


if __name__ == "__main__":
    unittest.main()
