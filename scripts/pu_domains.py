"""Power & Utilities semantic data-domain vocabulary + module mappings.

This is the shipped reference vocabulary for the domain layer added in
`server/migrations/002_domains.sql` (read that file's header for why the layer
exists). It is hand-authored to match the 146-module catalog in `pu_catalog.py`
rather than LLM-derived, so a customer starts from domain-accurate P&U semantics
instead of a cold-start guess.

TWO STRUCTURES
--------------
`DOMAINS`  — the closed vocabulary. One entry per semantic data need:
             (name, label, category, description, example_attributes)

`SERVES`   — which modules can supply each domain:
             domain_name -> [(source_category, module), ...]

             A domain served by several modules is the whole point: a use case
             needing `work_order_history` is satisfied by ERP Plant Maintenance
             OR EAM/APM Work Order Management, so a utility running Maximo
             instead of SAP PM no longer shows a false gap.

DESIGN NOTES
------------
- Granularity is set at "the level a use case would actually ask for". Too
  coarse ("operational data") and readiness means nothing; too fine (one domain
  per module) and the layer collapses back into uc_requires_asset with extra
  steps. ~45 domains over 146 modules lands in the useful middle.
- `category` values are constrained by the CHECK on data_domains.category. Keep
  this file and that constraint in sync.
- Every (source_category, module) pair in SERVES must exist in
  `pu_catalog.MODULES`. `scripts/seed_domains.py` validates this and fails loudly
  on a typo rather than silently dropping the mapping.
"""

# ---------------------------------------------------------------------------
# DOMAIN VOCABULARY
#   (name, label, category, description, example_attributes)
# ---------------------------------------------------------------------------
DOMAINS = [
    # ===== Asset =====
    ("asset_registry", "Asset Registry & Hierarchy", "asset",
     "Master inventory of physical assets with their parent/child hierarchy, class, and nameplate attributes.",
     "asset_id, asset_class, parent_asset_id, install_date, manufacturer, nameplate_rating"),
    ("asset_health_condition", "Asset Health & Condition Scores", "asset",
     "Derived condition/health indices and criticality rankings per asset.",
     "asset_id, health_index, criticality_rank, degradation_trend, scored_at"),
    ("work_order_history", "Work Order History", "asset",
     "Completed and in-flight corrective and preventive work orders with labor, cost, and findings.",
     "work_order_id, asset_id, order_type, opened_at, completed_at, labor_hours, total_cost"),
    ("maintenance_schedule", "Preventive Maintenance Schedules", "asset",
     "Planned maintenance intervals, triggers, and task templates per asset class.",
     "asset_class, pm_task_id, interval_days, trigger_type, last_performed, next_due"),
    ("failure_history", "Failure & Root-Cause History", "asset",
     "Recorded failure events, failure modes, and root-cause analysis outcomes.",
     "event_id, asset_id, failure_mode, detected_at, root_cause, corrective_action"),
    ("vibration_condition_monitoring", "Vibration & Condition Monitoring", "asset",
     "Time-series vibration, temperature, and other condition signals from rotating and static equipment.",
     "asset_id, sensor_id, timestamp, vibration_amplitude, bearing_temp, spectral_band"),
    ("spare_parts_inventory", "Spare Parts & Inventory", "asset",
     "Storeroom stock levels, part masters, reorder points, and lead times.",
     "part_id, description, on_hand_qty, reorder_point, lead_time_days, storeroom_id"),
    ("inspection_records", "Inspection & Field Rounds", "asset",
     "Operator rounds, inspection checklists, and field-collected observations.",
     "inspection_id, asset_id, performed_at, inspector, checklist_result, observations"),
    ("calibration_records", "Calibration Records", "asset",
     "Instrument calibration events, as-found/as-left values, and certification status.",
     "instrument_id, calibrated_at, as_found, as_left, tolerance, next_due"),

    # ===== Grid / network =====
    ("network_connectivity_model", "Network Connectivity Model", "grid",
     "Electrical topology — how devices, spans, and feeders connect, including phasing and switching state.",
     "device_id, from_node, to_node, feeder_id, phase, normal_state"),
    ("spatial_asset_location", "Spatial Asset Locations", "grid",
     "Geospatial coordinates and geometry for network assets and rights-of-way.",
     "asset_id, latitude, longitude, geometry_wkt, feeder_id, service_territory"),
    ("realtime_grid_telemetry", "Real-Time Grid Telemetry", "grid",
     "SCADA-sourced analog and status telemetry from substations, feeders, and devices.",
     "point_id, timestamp, value, quality, device_id, measurement_type"),
    ("grid_state_estimate", "Grid State Estimate", "grid",
     "State-estimator and power-flow solution output — bus voltages, angles, and branch flows.",
     "bus_id, timestamp, voltage_pu, angle_deg, branch_mw, branch_mvar, solution_id"),
    ("contingency_analysis_results", "Contingency Analysis Results", "grid",
     "N-1 and beyond security assessment results, violations, and limiting elements.",
     "contingency_id, timestamp, violated_element, loading_pct, limit_type, severity"),
    ("switching_operations", "Switching Operations & Device Status", "grid",
     "Switching orders, sequences, and recorded device open/close operations.",
     "operation_id, device_id, action, executed_at, operator, switching_order_id"),
    ("outage_records", "Outage & Interruption Records", "grid",
     "Sustained and momentary interruption events with cause, extent, customers affected, and restoration times.",
     "outage_id, feeder_id, start_time, restore_time, cause_code, customers_affected"),
    ("fault_events", "Fault & Protection Events", "grid",
     "Fault detections, relay operations, fault location estimates, and sequence-of-events records.",
     "fault_id, timestamp, feeder_id, fault_type, estimated_location_mi, relay_id"),
    ("power_quality", "Power Quality & Voltage Profile", "grid",
     "Voltage levels, VAR flow, sags/swells, harmonics, and regulation performance.",
     "point_id, timestamp, voltage_pu, thd_pct, sag_count, var_flow"),
    ("synchrophasor_measurements", "Synchrophasor (PMU) Measurements", "grid",
     "High-rate time-synchronized phasor measurements and derived oscillation/angle metrics.",
     "pmu_id, timestamp_utc, voltage_phasor, current_phasor, frequency, rocof"),
    ("der_registry_telemetry", "DER Registry & Telemetry", "grid",
     "Distributed energy resource registrations, nameplate data, setpoints, and operating telemetry.",
     "der_id, resource_type, nameplate_kw, interconnect_date, setpoint_kw, actual_kw"),
    ("vegetation_encroachment", "Vegetation & Encroachment", "grid",
     "Vegetation proximity to conductors from LiDAR, imagery, and field surveys, plus trim history.",
     "span_id, survey_date, clearance_ft, growth_rate, encroachment_risk, last_trim_date"),
    ("network_design_plans", "Network Design & Staking Plans", "grid",
     "Construction designs, staking sheets, and planned network changes not yet energized.",
     "design_id, feeder_id, work_type, planned_energize_date, engineer, status"),

    # ===== Operational (generation / process) =====
    ("process_historian_timeseries", "Process Historian Time-Series", "operational",
     "High-frequency plant process tags — temperatures, pressures, flows, levels, and equipment states.",
     "tag_name, timestamp, value, unit, quality, unit_id"),
    ("generation_output", "Generation Output & Availability", "operational",
     "Unit-level generation, capacity factor, availability, and derate/outage states.",
     "unit_id, timestamp, gross_mw, net_mw, availability_state, derate_mw"),
    ("thermal_performance", "Thermal Performance & Heat Rate", "operational",
     "Heat rate, efficiency, and thermodynamic performance metrics for thermal units.",
     "unit_id, period, heat_rate_btu_kwh, efficiency_pct, design_deviation"),
    ("renewable_resource_data", "Renewable Resource & Production", "operational",
     "Wind speed, irradiance, water head, and corresponding renewable production data.",
     "site_id, timestamp, wind_speed_ms, irradiance_wm2, head_ft, production_mw"),
    ("plant_alarms_events", "Plant Alarms & Events", "operational",
     "Alarm annunciations, trips, and sequence-of-events records from control systems.",
     "alarm_id, unit_id, timestamp, tag_name, priority, alarm_state, acknowledged_by"),
    ("nuclear_reactor_data", "Nuclear Reactor Process Data", "operational",
     "Reactor coolant, neutronics, containment, and safety-system process data.",
     "unit_id, timestamp, rcs_temp_f, neutron_flux, rod_position, containment_psi"),
    ("emissions_monitoring", "Emissions Monitoring", "regulatory",
     "Continuous emissions monitoring data, stack flow, opacity, and reportable emission totals.",
     "unit_id, timestamp, nox_lb_mmbtu, so2_lb_mmbtu, co2_tons, opacity_pct, stack_flow"),
    ("water_fuel_chemistry", "Water & Fuel Chemistry", "operational",
     "Laboratory sample results for water treatment, fuel quality, and chemistry control.",
     "sample_id, collected_at, sample_point, analyte, result_value, spec_limit"),

    # ===== Customer =====
    ("interval_meter_usage", "Interval Meter Usage", "customer",
     "Time-series interval consumption reads from AMI/smart meters.",
     "meter_id, interval_start, kwh, kw_demand, read_quality, channel"),
    ("meter_events", "Meter Events & Alarms", "customer",
     "Meter-generated events — outage/restore notifications, tampers, and communication failures.",
     "meter_id, event_time, event_type, severity, comm_status"),
    ("customer_accounts", "Customer Accounts & Premises", "customer",
     "Account, premise, and service-point master data including rate class and connection status.",
     "account_id, premise_id, service_point_id, rate_class, connect_date, status"),
    ("billing_transactions", "Billing & Payment Transactions", "customer",
     "Rendered bills, billing determinants, payments, arrears, and collections activity.",
     "bill_id, account_id, period_start, period_end, billed_kwh, amount_due, payment_date"),
    ("rate_tariff_structures", "Rate Schedules & Tariffs", "customer",
     "Tariff definitions, rate components, riders, and time-of-use period structures.",
     "rate_code, effective_date, component, charge_type, rate_value, tou_period"),
    ("customer_interactions", "Customer Interactions & Cases", "customer",
     "Contact-center calls, cases, complaints, and channel interaction history.",
     "interaction_id, account_id, occurred_at, channel, reason_code, resolution"),
    ("service_orders", "Service Orders & Field Service", "customer",
     "Customer-initiated field work — connects, disconnects, meter exchanges, and investigations.",
     "service_order_id, account_id, order_type, scheduled_date, completed_date, technician"),
    ("demand_response_events", "Demand Response & Load Control", "customer",
     "DR and DSM program enrollments, dispatch events, and measured load reduction.",
     "program_id, account_id, event_start, event_end, curtailed_kw, performance_pct"),
    ("revenue_protection", "Revenue Protection & Theft Signals", "customer",
     "Non-technical loss investigations, tamper signals, and unbilled-usage findings.",
     "case_id, account_id, meter_id, opened_at, tamper_flag, unbilled_kwh, disposition"),
    ("remote_service_control", "Remote Service Control", "customer",
     "Remote disconnect/reconnect commands, confirmations, and field-order fallbacks.",
     "command_id, meter_id, account_id, command_type, issued_at, confirmed_at, result"),

    # ===== Market =====
    ("market_prices", "Market & Locational Prices", "market",
     "Day-ahead and real-time LMPs with energy/congestion/loss components by settlement point.",
     "settlement_point, interval_start, market_type, lmp, energy_component, congestion_component"),
    ("market_bids_offers", "Bids, Offers & Awards", "market",
     "Submitted supply offers, demand bids, virtual positions, and cleared awards.",
     "bid_id, resource_id, interval_start, price, quantity_mw, cleared_mw, product"),
    ("ancillary_services", "Ancillary Services & Reserves", "market",
     "Regulation, reserve, and other ancillary-service requirements, awards, and performance.",
     "product, interval_start, requirement_mw, awarded_mw, clearing_price, performance_score"),
    ("settlements_invoices", "Market Settlements & Invoices", "market",
     "ISO/RTO settlement statements, charge codes, and invoice reconciliation detail.",
     "statement_date, charge_code, quantity, rate, amount, resource_id"),
    ("transmission_rights", "Transmission Rights & Congestion", "market",
     "FTR/CRR positions, auction results, and congestion-revenue outcomes.",
     "ftr_id, source, sink, term, cleared_price, mw, settled_revenue"),
    ("load_forecast", "Load & Demand Forecast", "market",
     "Short- and long-term system, zonal, and feeder-level demand forecasts with actuals for scoring.",
     "forecast_for, issued_at, zone_id, forecast_mw, actual_mw, horizon_hours"),
    ("fuel_supply", "Fuel Supply, Contracts & Inventory", "market",
     "Fuel inventory levels, contract terms, nominations, deliveries, delivered cost, and spent-fuel storage.",
     "fuel_type, as_of_date, inventory_tons, contract_id, nominated_qty, delivered_cost"),
    ("interchange_schedules", "Interchange & Tie Schedules", "market",
     "Scheduled and actual interchange across tie lines, including e-Tags and transaction schedules.",
     "schedule_id, interval_start, tie_point, scheduled_mw, actual_mw, counterparty"),

    # ===== External =====
    ("weather_observed", "Observed Weather", "external",
     "Historical and current observed weather at system and site level.",
     "station_id, timestamp, temp_f, wind_speed_ms, precip_in, humidity_pct"),
    ("weather_forecast", "Weather Forecast", "external",
     "Numerical and site-specific weather forecasts including severe-weather and lightning tracks.",
     "issued_at, valid_for, location_id, temp_f, wind_speed_ms, storm_probability"),

    # ===== Financial =====
    ("financial_actuals", "Financial Actuals & Ledger", "financial",
     "General-ledger actuals, cost-center spend, and revenue by period.",
     "period, cost_center, account_code, actual_amount, budget_amount, variance"),
    ("capital_projects", "Capital Projects & Budgets", "financial",
     "Capital project definitions, WBS structure, budgets, commitments, and spend-to-date.",
     "project_id, wbs_element, budget_amount, committed_amount, spend_to_date, in_service_date"),
    ("asset_accounting", "Asset Accounting & Depreciation", "financial",
     "Fixed-asset book values, depreciation schedules, and retirement units.",
     "asset_id, acquisition_cost, book_value, depreciation_method, useful_life_years"),
    ("procurement_spend", "Procurement & Supplier Spend", "financial",
     "Purchase orders, supplier master data, and category spend.",
     "po_id, supplier_id, category, order_date, amount, delivery_date"),

    # ===== Workforce / safety =====
    ("workforce_records", "Workforce & Skills", "workforce",
     "Employee and contractor rosters, certifications, skills, and attrition risk.",
     "employee_id, role, hire_date, certifications, skill_codes, retirement_eligible_date"),
    ("crew_scheduling", "Crew Scheduling & Availability", "workforce",
     "Crew assignments, shift schedules, availability, and mobile dispatch status.",
     "crew_id, shift_date, assigned_work_order, availability_state, current_location"),
    ("timesheet_labor", "Timesheet & Labor Charging", "workforce",
     "Recorded labor hours charged to work orders, projects, and cost centers.",
     "employee_id, work_date, hours, work_order_id, cost_center, pay_type"),
    ("safety_incidents", "Safety Incidents & Observations", "safety",
     "Recordable incidents, near-misses, safety observations, and OSHA classification.",
     "incident_id, occurred_at, location, incident_type, severity, osha_recordable"),
    ("work_clearance_permits", "Work Clearance & Permits", "safety",
     "Lockout/tagout records, work clearances, and safety permits.",
     "clearance_id, asset_id, issued_at, released_at, permit_type, issuer"),
    ("radiation_dosimetry", "Radiation Dose & Contamination", "safety",
     "Personnel dose records, area radiation monitoring, contamination surveys, and ALARA planning.",
     "worker_id, period, dose_mrem, area_monitor_id, survey_result, rwp_id"),

    # ===== Regulatory =====
    ("regulatory_filings", "Regulatory Filings & Commitments", "regulatory",
     "Filed regulatory submissions, docket references, commitments, and their due dates.",
     "filing_id, docket_number, agency, filed_date, commitment_id, due_date, status"),
    ("compliance_documents", "Procedures & Controlled Documents", "regulatory",
     "Controlled procedures, engineering drawings, and records under retention policy.",
     "document_id, doc_type, revision, effective_date, owner, retention_class"),
    ("reliability_metrics", "Reliability Metrics & Reporting", "regulatory",
     "Calculated reliability indices (SAIDI/SAIFI/CAIDI/MAIFI) and regulatory reliability reports.",
     "reporting_period, service_territory, saidi, saifi, caidi, maifi, excluded_events"),
]

# ---------------------------------------------------------------------------
# MODULE -> DOMAIN MAPPINGS
#   domain_name -> [(source_category, module), ...]
# Every pair must exist in pu_catalog.MODULES; seed_domains.py validates.
# ---------------------------------------------------------------------------
SERVES = {
    # --- Asset ---
    "asset_registry": [
        ("ERP", "Plant Maintenance / EAM"),
        ("EAM/APM", "Asset Registry & Hierarchy"),
        ("GIS", "Asset Inventory (Spatial)"),
    ],
    "asset_health_condition": [
        ("EAM/APM", "Asset Performance & Health Scoring"),
        ("EAM/APM", "Condition Monitoring / PdM"),
        ("EAM/APM", "Reliability / RCM"),
    ],
    "work_order_history": [
        ("ERP", "Plant Maintenance / EAM"),
        ("EAM/APM", "Work Order Management"),
        ("EAM/APM", "Mobile Workforce Management"),
    ],
    "maintenance_schedule": [
        ("EAM/APM", "Preventive Maintenance Scheduling"),
        ("EAM/APM", "Reliability Library / Failure Modes"),
    ],
    "failure_history": [
        ("EAM/APM", "Failure Analysis / RCA"),
        ("EAM/APM", "Reliability Library / Failure Modes"),
        ("EAM/APM", "Reliability / RCM"),
    ],
    "vibration_condition_monitoring": [
        ("EAM/APM", "Vibration / Condition Routes"),
        ("EAM/APM", "Condition Monitoring / PdM"),
        ("Data Historian", "Vibration / Condition Monitoring"),
    ],
    "spare_parts_inventory": [
        ("ERP", "Materials Management"),
        ("ERP", "Inventory / Warehouse"),
        ("EAM/APM", "Spare Parts / Inventory"),
    ],
    "inspection_records": [
        ("EAM/APM", "Inspection / Rounds"),
        ("ERP", "Quality Management"),
    ],
    "calibration_records": [
        ("EAM/APM", "Calibration Management"),
        ("CEMS", "QA/QC (RATA/Calibration)"),
    ],

    # --- Grid ---
    "network_connectivity_model": [
        ("GIS", "Electric Network Model / Connectivity"),
        ("GIS", "Utility Network (ArcGIS)"),
        ("EMS (Transmission)", "Network Model / CIM"),
        ("EMS (Transmission)", "Network Topology Processor"),
    ],
    "spatial_asset_location": [
        ("GIS", "Asset Inventory (Spatial)"),
        ("GIS", "Mobile GIS / Field Maps"),
    ],
    "realtime_grid_telemetry": [
        ("EMS (Transmission)", "T-SCADA"),
        ("ADMS (Distribution)", "D-SCADA"),
        ("Standalone SCADA", "Real-Time Telemetry & Control"),
    ],
    "grid_state_estimate": [
        ("EMS (Transmission)", "State Estimation"),
        ("ADMS (Distribution)", "Distribution State Estimation"),
        ("ADMS (Distribution)", "Distribution Power Flow"),
        ("EMS (Transmission)", "Optimal Power Flow"),
    ],
    "contingency_analysis_results": [
        ("EMS (Transmission)", "Real-Time Contingency Analysis"),
    ],
    "switching_operations": [
        ("ADMS (Distribution)", "Switching Sequence Management"),
        ("ADMS (Distribution)", "DMS (Distribution Management)"),
        ("EMS (Transmission)", "Disturbance / SOE Analysis"),
    ],
    "outage_records": [
        ("ADMS (Distribution)", "OMS (Outage Management)"),
        ("Standalone OMS", "Outage Events & Restoration"),
        ("ADMS (Distribution)", "Storm / Damage Assessment"),
    ],
    "fault_events": [
        ("ADMS (Distribution)", "Fault Location Analysis"),
        ("ADMS (Distribution)", "FLISR"),
        ("EMS (Transmission)", "Disturbance / SOE Analysis"),
    ],
    "power_quality": [
        ("ADMS (Distribution)", "VVO / CVR"),
        ("Meter/AMI/MDM", "Voltage / VAR Telemetry"),
        ("EMS (Transmission)", "Voltage / VAR Scheduling"),
    ],
    "synchrophasor_measurements": [
        ("PMU/Synchrophasor", "Phasor Measurement"),
        ("PMU/Synchrophasor", "Phasor Data Concentrator"),
        ("PMU/Synchrophasor", "Oscillation Detection"),
        ("PMU/Synchrophasor", "Linear State Measurement"),
        ("PMU/Synchrophasor", "Islanding / Angle Separation"),
    ],
    "der_registry_telemetry": [
        ("DERMS", "DER Registration & Modeling"),
        ("DERMS", "DER Dispatch & Control"),
        ("DERMS", "Virtual Power Plant Orchestration"),
        ("DERMS", "EV Charging Management"),
    ],
    "vegetation_encroachment": [
        ("GIS", "Vegetation Management"),
    ],
    "network_design_plans": [
        ("GIS", "Staking / Design"),
        ("ERP", "Project System"),
    ],

    # --- Operational ---
    "process_historian_timeseries": [
        ("Data Historian", "Steam Turbine"),
        ("Data Historian", "Boiler / HRSG"),
        ("Data Historian", "Feedwater / Condensate"),
        ("Data Historian", "Balance of Plant"),
        ("Data Historian", "Combustion / Burner Management"),
    ],
    "generation_output": [
        ("Data Historian", "Generator / Excitation"),
        ("EMS (Transmission)", "Economic Dispatch"),
        ("EMS (Transmission)", "Unit Commitment"),
        ("EMS (Transmission)", "AGC / LFC"),
    ],
    "thermal_performance": [
        ("Data Historian", "Steam Turbine"),
        ("Data Historian", "Boiler / HRSG"),
        ("Data Historian", "Feedwater / Condensate"),
    ],
    "renewable_resource_data": [
        ("Data Historian", "Wind Turbine"),
        ("Data Historian", "Solar Inverter / PV"),
        ("Data Historian", "Hydro Unit"),
        ("Weather", "Renewable Generation Forecasting"),
    ],
    "plant_alarms_events": [
        ("Data Historian", "Combustion / Burner Management"),
        ("Data Historian", "Emissions Process Tags"),
        ("EMS (Transmission)", "Disturbance / SOE Analysis"),
    ],
    "nuclear_reactor_data": [
        ("Data Historian", "Reactor Coolant System"),
        ("Data Historian", "Reactor Core / Neutronics"),
        ("Data Historian", "Steam Generator / Secondary"),
        ("Data Historian", "Pressurizer"),
        ("Data Historian", "Containment"),
        ("Data Historian", "Safety Systems (ECCS/RHR)"),
    ],
    "emissions_monitoring": [
        ("CEMS", "Gas Analyzers (NOx/SO2/CO2/O2)"),
        ("CEMS", "Opacity / Particulate"),
        ("CEMS", "Stack Flow"),
        ("CEMS", "DAHS (Data Acquisition)"),
        ("CEMS", "Part 75 Reporting / EDR"),
        ("Data Historian", "Emissions Process Tags"),
    ],
    "water_fuel_chemistry": [
        ("LIMS", "Sample Management"),
        ("LIMS", "Water / Fuel Chemistry"),
        ("LIMS", "Results Management"),
        ("LIMS", "QA/QC / Control Charts"),
        ("Fuel Management", "Fuel Quality / Blending"),
    ],

    # --- Customer ---
    "interval_meter_usage": [
        ("Meter/AMI/MDM", "Interval Usage Collection"),
        ("Meter/AMI/MDM", "MDM Repository"),
        ("Meter/AMI/MDM", "VEE (Validation/Estimation/Editing)"),
        ("Meter/AMI/MDM", "Head-End System"),
    ],
    "meter_events": [
        ("Meter/AMI/MDM", "Meter Events / Alarms"),
        ("Meter/AMI/MDM", "Head-End System"),
    ],
    "customer_accounts": [
        ("CIS/Billing", "Customer Accounts"),
        ("CIS/Billing", "CRM / Contact Center"),
    ],
    "billing_transactions": [
        ("CIS/Billing", "Billing Engine / Determinants"),
        ("CIS/Billing", "Payments / Collections"),
    ],
    "rate_tariff_structures": [
        ("CIS/Billing", "Rate Schedules / Tariffs"),
    ],
    "customer_interactions": [
        ("CIS/Billing", "CRM / Contact Center"),
    ],
    "service_orders": [
        ("CIS/Billing", "Service Orders / Field Service"),
    ],
    "demand_response_events": [
        ("DERMS", "Demand Response / Load Control"),
        ("DERMS", "EV Charging Management"),
        ("CIS/Billing", "Programs / DSM Enrollment"),
    ],
    "revenue_protection": [
        ("CIS/Billing", "Revenue Protection / Theft"),
        ("Meter/AMI/MDM", "Meter Events / Alarms"),
    ],
    "remote_service_control": [
        ("Meter/AMI/MDM", "Remote Disconnect / Reconnect"),
        ("CIS/Billing", "Service Orders / Field Service"),
    ],

    # --- Market ---
    "market_prices": [
        ("Market/ISO Feed", "Day-Ahead LMP"),
        ("Market/ISO Feed", "Real-Time LMP"),
        ("Market/ISO Feed", "LMP Components (Energy/Congestion/Loss)"),
        ("Market/ISO Feed", "Capacity Market"),
    ],
    "market_bids_offers": [
        ("Market/ISO Feed", "Bids & Offers"),
        ("Market/ISO Feed", "Virtual Bids (INC/DEC)"),
    ],
    "ancillary_services": [
        ("Market/ISO Feed", "Ancillary Services"),
        ("EMS (Transmission)", "AGC / LFC"),
    ],
    "load_forecast": [
        ("EMS (Transmission)", "Load Forecasting"),
        ("ADMS (Distribution)", "Distribution Load Forecasting"),
        ("Market/ISO Feed", "ISO Load & Gen Forecast"),
        ("Meter/AMI/MDM", "Load Profiles"),
    ],
    "fuel_supply": [
        ("Fuel Management", "Fossil Fuel Inventory"),
        ("Fuel Management", "Fuel Contracts / Procurement"),
        ("Fuel Management", "Gas Nominations"),
        ("Fuel Management", "Nuclear Fuel Cycle"),
        ("Fuel Management", "Core Design / Reload"),
        ("Fuel Management", "Spent Fuel Management"),
    ],
    "interchange_schedules": [
        ("EMS (Transmission)", "Interchange Scheduling"),
        ("Market/ISO Feed", "Interchange / Tie Schedules"),
    ],

    # --- External ---
    "weather_observed": [
        ("Weather", "Site-Specific Forecasts"),
        ("Weather", "Lightning Detection"),
    ],
    "weather_forecast": [
        ("Weather", "NWP Forecasts"),
        ("Weather", "Site-Specific Forecasts"),
        ("Weather", "Storm / Severe Weather Tracking"),
        ("Weather", "Renewable Generation Forecasting"),
        ("Weather", "Icing / Wind Loading"),
    ],

    # --- Financial ---
    "financial_actuals": [
        ("ERP", "Finance & Controlling"),
    ],
    "capital_projects": [
        ("ERP", "Project System"),
    ],
    "asset_accounting": [
        ("ERP", "Asset Accounting"),
    ],
    "procurement_spend": [
        ("ERP", "Procurement / Sourcing"),
        ("ERP", "Materials Management"),
    ],

    # --- Workforce / safety ---
    "workforce_records": [
        ("ERP", "Human Capital Management"),
    ],
    "crew_scheduling": [
        ("EAM/APM", "Scheduling & Crew Optimization"),
        ("EAM/APM", "Mobile Workforce Management"),
        ("ADMS (Distribution)", "Storm / Damage Assessment"),
    ],
    "timesheet_labor": [
        ("ERP", "Human Capital Management"),
    ],
    "safety_incidents": [
        ("ERP", "Environment, Health & Safety"),
    ],
    "work_clearance_permits": [
        ("ERP", "Work Clearance Management"),
    ],
    "radiation_dosimetry": [
        ("Radiation/Dosimetry", "Personnel Dosimetry"),
        ("Radiation/Dosimetry", "RWP / Access Control"),
        ("Radiation/Dosimetry", "Area Radiation Monitors"),
        ("Radiation/Dosimetry", "Effluent Monitoring"),
        ("Radiation/Dosimetry", "Contamination / Survey"),
        ("Radiation/Dosimetry", "ALARA Planning"),
    ],

    # --- Regulatory ---
    "regulatory_filings": [
        ("Document/Content Mgmt", "Regulatory Document Mgmt"),
        ("Document/Content Mgmt", "Commitment Tracking"),
        ("CEMS", "Part 75 Reporting / EDR"),
    ],
    "compliance_documents": [
        ("Document/Content Mgmt", "Procedures Management"),
        ("Document/Content Mgmt", "Engineering Drawings / CAD"),
        ("Document/Content Mgmt", "Records Management / Retention"),
    ],
    "reliability_metrics": [
        ("ADMS (Distribution)", "OMS (Outage Management)"),
        ("Standalone OMS", "Outage Events & Restoration"),
    ],

    "settlements_invoices": [
        ("Market/ISO Feed", "Settlements / Invoicing"),
    ],
    "transmission_rights": [
        ("Market/ISO Feed", "FTR / CRR"),
        ("Market/ISO Feed", "Congestion / Constraints"),
    ],
}
