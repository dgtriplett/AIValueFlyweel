-- AI Value Flywheel schema — CHUNK L: asset detail content population.
-- Idempotent: safe to re-run (only updates NULL/empty values).
--
-- Populates the four descriptive columns added in migration 016 (provides,
-- refresh_cadence, steward, source_of_record) for the 146 catalog data assets,
-- deriving values from their existing metadata (source_system, module, owning_lob,
-- source_category).
--
-- Also populates uc_requires_asset.rationale for edges where a concise generic
-- rationale can be confidently derived.
--
-- IDEMPOTENT: uses UPDATE ... WHERE (field IS NULL OR field='') so re-running won't
-- clobber user edits. Matches assets by natural key (source_system, module) to work
-- across environments with different ids.

-- Helper function to safely update only unpopulated fields
CREATE OR REPLACE FUNCTION update_asset_detail(
    p_source_system TEXT,
    p_module TEXT,
    p_provides TEXT DEFAULT NULL,
    p_steward TEXT DEFAULT NULL,
    p_source_of_record TEXT DEFAULT NULL,
    p_refresh_cadence TEXT DEFAULT NULL
) RETURNS void AS $$
BEGIN
    UPDATE data_assets
    SET provides = COALESCE(NULLIF(provides, ''), p_provides),
        steward = COALESCE(NULLIF(steward, ''), p_steward),
        source_of_record = COALESCE(NULLIF(source_of_record, ''), p_source_of_record),
        refresh_cadence = COALESCE(NULLIF(refresh_cadence, ''), p_refresh_cadence)
    WHERE source_system = p_source_system
      AND module = p_module
      AND (provides IS NULL OR provides = ''
           OR steward IS NULL OR steward = ''
           OR source_of_record IS NULL OR source_of_record = ''
           OR refresh_cadence IS NULL OR refresh_cadence = '');
END;
$$ LANGUAGE plpgsql;

-- ==============================================================================
-- ERP (11 assets) — Corporate Services
-- ==============================================================================
SELECT update_asset_detail(
    'ERP', 'Plant Maintenance / EAM',
    'Work orders, equipment master records, preventive maintenance schedules, and maintenance history for physical assets — the system of record for plant maintenance planning and execution.',
    'Corporate Services team',
    'SAP ERP — Plant Maintenance module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Materials Management',
    'Inventory management, purchasing, materials planning, and warehouse operations — tracks spare parts, consumables, and supply chain logistics for operations and maintenance.',
    'Corporate Services team',
    'SAP ERP — Materials Management module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Finance & Controlling',
    'General ledger, accounts payable/receivable, cost accounting, budgeting, and financial reporting — the authoritative source for corporate financial transactions and reporting.',
    'Corporate Services team',
    'SAP ERP — Finance & Controlling module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Asset Accounting',
    'Fixed asset register, depreciation calculations, asset lifecycle tracking, and capitalization — manages the book value and financial treatment of utility infrastructure and equipment.',
    'Corporate Services team',
    'SAP ERP — Asset Accounting module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Project System',
    'Capital project management, work breakdown structures, project budgets, and actuals — tracks major construction, refurbishment, and deployment initiatives.',
    'Corporate Services team',
    'SAP ERP — Project System module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Human Capital Management',
    'Employee records, payroll, time tracking, benefits, training, and workforce planning — the system of record for all personnel data.',
    'Corporate Services team',
    'SAP ERP — Human Capital Management module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Sales & Distribution',
    'Customer orders, contract management, pricing, and billing determinants for large commercial and industrial accounts.',
    'Customer team',
    'SAP ERP — Sales & Distribution module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Production Planning',
    'Generation unit scheduling, fuel consumption planning, and production order management for power plants.',
    'Generation team',
    'SAP ERP — Production Planning module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Quality Management',
    'Inspection plans, test results, quality notifications, and compliance documentation for equipment, materials, and processes.',
    'Corporate Services team',
    'SAP ERP — Quality Management module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Environment, Health & Safety',
    'Incident tracking, safety inspections, hazardous material management, and EHS compliance reporting.',
    'Corporate Services team',
    'SAP ERP — EHS module',
    'Daily batch'
);

SELECT update_asset_detail(
    'ERP', 'Supply Chain & Procurement',
    'Vendor management, purchase requisitions, RFQs, contract tracking, and procurement analytics — consolidates all external supplier interactions.',
    'Corporate Services team',
    'SAP ERP — Supply Chain & Procurement module',
    'Daily batch'
);

-- ==============================================================================
-- Data Historian (17 assets) — Generation
-- ==============================================================================
SELECT update_asset_detail(
    'Data Historian', 'Steam Turbine',
    'High-frequency time-series data from steam turbines including pressures, temperatures, vibration, and governor signals — critical for performance monitoring and predictive maintenance.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Generator / Excitation',
    'Generator output, voltage, current, excitation system parameters, and protective relay signals — real-time monitoring of electrical generation performance.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Boiler / HRSG',
    'Boiler drum levels, steam flows, combustion parameters, heat recovery steam generator (HRSG) metrics — tracks thermal efficiency and emissions precursors.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Gas Turbine',
    'Combustor temperatures, compressor pressures, fuel flows, and exhaust conditions for gas turbine units — used for performance trending and anomaly detection.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Balance of Plant',
    'Auxiliary equipment including pumps, fans, cooling towers, condensers, and feedwater heaters — monitors plant efficiency and identifies underperforming auxiliary systems.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Hydro Turbine & Generator',
    'Water flows, head pressures, wicket gate positions, generator output, and reservoir levels for hydroelectric units.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Nuclear Reactor Core',
    'Core neutron flux, control rod positions, reactor coolant temperatures and flows, and power distribution — tightly monitored for safety and operational limits.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / sub-second to 1-second'
);

SELECT update_asset_detail(
    'Data Historian', 'Nuclear Steam & Secondary',
    'Steam generator levels, feedwater flows, turbine conditions, and secondary-side chemistry for nuclear plants.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Nuclear Safety Systems',
    'Emergency core cooling, containment monitoring, radiation area monitors, and safety injection systems — compliance-critical data for NRC reporting.',
    'Regulatory team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / sub-second to 1-second'
);

SELECT update_asset_detail(
    'Data Historian', 'Coal Handling & Preparation',
    'Coal quality (BTU, ash, sulfur), conveyor systems, pulverizers, and fuel yard inventory — impacts generation economics and emissions.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-minute to 5-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Emissions (NOx/SO2/Particulate)',
    'Continuous emissions monitoring data including NOx, SO2, particulate matter, and opacity — required for EPA and state air quality compliance.',
    'Regulatory team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Water Treatment / Chemistry',
    'Boiler water chemistry, cooling water treatment, pH, conductivity, and dissolved oxygen — prevents corrosion and scale buildup.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 5-minute to 15-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Solar Inverter & String',
    'DC string voltages, currents, inverter output, module temperatures, and irradiance sensors for photovoltaic arrays.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Wind Turbine',
    'Rotor speed, pitch angles, nacelle position, generator output, vibration, and meteorological sensors for wind farms.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Battery Energy Storage',
    'State of charge, cell voltages, temperatures, charge/discharge rates, and inverter status for grid-scale battery systems.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Fuel Oil / Gas Supply',
    'Natural gas pipeline pressures, flows, heating value, fuel oil tank levels, and delivery schedules — critical for dispatch and cost modeling.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-minute to 5-minute'
);

SELECT update_asset_detail(
    'Data Historian', 'Plant Efficiency & Performance',
    'Calculated heat rates, unit efficiency, capacity factors, and performance deviations from design — key metrics for asset optimization.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 5-minute to hourly'
);

-- ==============================================================================
-- EAM/APM (14 assets) — Corporate Services
-- ==============================================================================
SELECT update_asset_detail(
    'EAM/APM', 'Asset Registry & Hierarchy',
    'Master equipment list, asset hierarchy, functional locations, and equipment specifications — the authoritative registry for all utility assets.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Work Order Management',
    'Work orders, job plans, labor, materials, and costs for corrective, preventive, and capital work.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Preventive Maintenance Scheduling',
    'PM task lists, calendars, meter-based triggers, and completion history — ensures regulatory compliance and reduces unplanned outages.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Condition Monitoring Integration',
    'Vibration, thermography, oil analysis, and predictive analytics results linked to asset records for condition-based maintenance.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Reliability & Failure Analysis',
    'Failure modes, root cause analysis, reliability metrics (MTBF, MTTR), and criticality assessments — drives maintenance strategy optimization.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Materials & Inventory (EAM)',
    'Spare parts catalogs, storeroom inventory, reorder points, and equipment bill of materials — tightly integrated with work orders.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Warranty & Vendor Contract Tracking',
    'Equipment warranties, service contracts, SLAs, and vendor performance — ensures cost recovery and contract compliance.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Asset Health Scores & Risk',
    'Composite health indices, probability of failure, consequence of failure, and risk matrices — prioritizes capital and maintenance investments.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Document Management (Drawings/Manuals)',
    'As-built drawings, O&M manuals, vendor documentation, and calibration certificates linked to asset records.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Capital Projects & Replacements',
    'Asset replacement planning, capital forecasts, project tracking, and cost justifications — bridges EAM and financial planning.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Labor Management & Scheduling',
    'Crew scheduling, skill tracking, time reporting, and workforce availability — optimizes labor utilization.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Calibration & Testing Records',
    'Calibration schedules, test results, out-of-tolerance logs, and metrology standards for instruments and protective devices.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail(
    'EAM/APM', 'Mobile EAM (Field Data Collection)',
    'Field technician apps for work order updates, time capture, photo/sketch attachments, and offline sync — extends EAM to field crews.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Real-time / on-demand'
);

SELECT update_asset_detail(
    'EAM/APM', 'Safety Permits & Compliance',
    'Safety permits, lockout/tagout tracking, confined space entry, and hot work permits — ensures field safety compliance.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

-- ==============================================================================
-- EMS (Transmission) (13 assets) — Transmission
-- ==============================================================================
SELECT update_asset_detail(
    'EMS (Transmission)', 'State Estimator',
    'Real-time network model solving for bus voltages, line flows, and losses across the transmission grid — foundational for all EMS applications.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 30-second to 2-minute'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'SCADA Points',
    'Supervisory control and data acquisition telemetry including breaker status, MW/MVAR flows, voltages, and frequency from substations.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 2-second to 4-second scan'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Generation Dispatch & AGC',
    'Automatic generation control signals, economic dispatch set points, and generator status — balances load and generation in real-time.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 4-second AGC cycle'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Contingency Analysis',
    'Pre-contingency and post-contingency power flow analysis (N-1, N-2) for transmission reliability and NERC compliance.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 5-minute to 15-minute'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Outage Management (Transmission)',
    'Planned transmission outage schedules, switching orders, clearances, and outage coordination — prevents reliability violations.',
    'Transmission team',
    'Energy Management System (EMS)',
    'On-demand / real-time updates'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Interchange Scheduling',
    'Scheduled energy transactions with neighboring utilities and ISOs, e-tags, and inadvertent interchange accounting.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / hourly updates'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Voltage Control & VAR Optimization',
    'Capacitor bank control, reactor switching, and transformer tap control to maintain voltage within NERC standards.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 1-minute to 5-minute'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Load Forecasting (Transmission)',
    'Short-term transmission-level load forecasts (hourly, daily, weekly) for scheduling and dispatch planning.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Hourly updates'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Alarm & Event Management',
    'Alarm prioritization, event sequencing, and operator logs — critical for situational awareness during disturbances.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / sub-second'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Network Model & Topology',
    'Electrical connectivity model, substation one-lines, breaker-to-node mappings, and impedance parameters — the static network foundation for all EMS analysis.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Weekly updates / on-demand for topology changes'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Real-Time Pricing & Market Interface',
    'Locational marginal pricing (LMP) feeds from ISOs, bid/offer submission, and market settlement data for vertically integrated or market-participating utilities.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 5-minute LMP'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Power System Visualization',
    'Geographic and schematic displays of the transmission system, situational awareness dashboards, and operator HMI.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time display updates'
);

SELECT update_asset_detail(
    'EMS (Transmission)', 'Short-Circuit & Protection Coordination',
    'Fault current calculations, protective relay settings, and coordination studies — ensures correct relay operation.',
    'Transmission team',
    'Energy Management System (EMS)',
    'On-demand / quarterly reviews'
);

-- ==============================================================================
-- Market/ISO Feed (12 assets) — Generation
-- ==============================================================================
SELECT update_asset_detail(
    'Market/ISO Feed', 'Day-Ahead LMP',
    'Day-ahead locational marginal prices for all nodes and zones — used for generation bidding and forward scheduling.',
    'Generation team',
    'ISO/RTO market data feed (e.g., CAISO, PJM, ERCOT)',
    'Daily (posted morning of operating day)'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Real-Time LMP',
    'Real-time (5-minute) locational marginal prices reflecting actual system conditions — critical for dispatch economics and settlement.',
    'Generation team',
    'ISO/RTO market data feed',
    '5-minute intervals'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'LMP Components (Energy/Congestion/Loss)',
    'Decomposed LMP into energy, congestion, and marginal loss components — enables detailed financial analysis and hedging strategies.',
    'Generation team',
    'ISO/RTO market data feed',
    '5-minute intervals'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Ancillary Services Prices',
    'Market clearing prices for regulation, spinning reserve, non-spinning reserve, and replacement reserves.',
    'Generation team',
    'ISO/RTO market data feed',
    'Hourly / 5-minute'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Congestion Revenue Rights (CRRs)',
    'Financial transmission rights, auction results, and CRR settlement data — hedges basis risk between generation and load.',
    'Generation team',
    'ISO/RTO market data feed',
    'Monthly auctions / daily settlements'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Load Forecast (ISO)',
    'ISO system-wide and zonal load forecasts used for market clearing and reserve requirements.',
    'Generation team',
    'ISO/RTO market data feed',
    'Hourly updates'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Unit Commitment & Dispatch Instructions',
    'Start-up, shut-down, and economic dispatch set points issued by the ISO to generation units.',
    'Generation team',
    'ISO/RTO market data feed',
    'Real-time / 5-minute dispatch'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Market Clearing Results',
    'Day-ahead and hour-ahead market awards, cleared volumes, and schedules — confirms accepted bids and financial obligations.',
    'Generation team',
    'ISO/RTO market data feed',
    'Daily / hourly'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Renewable Forecast (Wind/Solar)',
    'ISO-published forecasts of wind and solar generation for market clearing and reliability planning.',
    'Generation team',
    'ISO/RTO market data feed',
    'Hourly updates / 5-minute actuals'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Transmission Constraints & Limits',
    'Binding transmission constraints, flowgate limits, and security-constrained dispatch parameters.',
    'Transmission team',
    'ISO/RTO market data feed',
    'Real-time / hourly'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Settlement & Invoice Data',
    'Energy settlement statements, uplift charges, make-whole payments, and invoice line items.',
    'Generation team',
    'ISO/RTO market data feed',
    'Monthly settlements / weekly preliminary'
);

SELECT update_asset_detail(
    'Market/ISO Feed', 'Outage Schedules (Market)',
    'ISO-approved generator and transmission outage schedules — coordinated to maintain grid reliability.',
    'Generation team',
    'ISO/RTO market data feed',
    'Daily updates / real-time changes'
);

-- ==============================================================================
-- ADMS (Distribution) (11 assets) — Distribution
-- ==============================================================================
SELECT update_asset_detail(
    'ADMS (Distribution)', 'Distribution SCADA',
    'Supervisory control and data acquisition for distribution feeders, substations, reclosers, capacitor banks, and voltage regulators.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / 15-second to 1-minute'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Distribution State Estimator',
    'Real-time distribution network model solving for voltages, currents, and losses — enables operational visibility below the substation.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / 1-minute to 5-minute'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Fault Location, Isolation & Service Restoration (FLISR)',
    'Automated fault detection, isolation via remote switching, and service restoration to minimize outage duration.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / sub-second detection'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Volt-VAR Optimization (VVO)',
    'Automated control of capacitor banks, voltage regulators, and conservation voltage reduction (CVR) to reduce losses and improve power quality.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / 1-minute to 5-minute optimization cycles'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Outage Management System (OMS) Integration',
    'Bi-directional integration between ADMS and OMS for predicted outage locations, switching plans, and estimated restoration times.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time integration'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'DER Management & Visibility',
    'Monitoring and control of distributed energy resources (solar, storage, EV chargers) at the grid edge — critical for hosting capacity and stability.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / 1-minute'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Load Forecasting (Distribution)',
    'Feeder-level and substation load forecasts for planning and operational decisions.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Hourly updates'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Distribution Power Flow',
    'Offline and near-real-time power flow analysis for distribution planning, capacity studies, and what-if scenarios.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'On-demand / hourly background runs'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Switch Order Management',
    'Planned switching procedures, clearance management, and tagging for distribution maintenance and construction.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'On-demand / real-time updates'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Crew Management & Dispatch',
    'Mobile workforce location, work assignment, and dispatch integration with OMS for outage restoration.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / on-demand'
);

SELECT update_asset_detail(
    'ADMS (Distribution)', 'Distribution Network Model',
    'As-maintained electrical connectivity model of the distribution grid including feeders, transformers, protective devices, and customer connections — synchronized with GIS.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Daily sync / on-demand for model changes'
);

-- ==============================================================================
-- Meter/AMI/MDM (8 assets) — Customer
-- ==============================================================================
SELECT update_asset_detail(
    'Meter/AMI/MDM', 'Head-End System',
    'AMI network management and meter communication infrastructure — orchestrates interval data collection and remote commands.',
    'Customer team',
    'AMI Head-End System (HES)',
    'Real-time / 15-minute intervals'
);

SELECT update_asset_detail(
    'Meter/AMI/MDM', 'Interval Usage Collection',
    '15-minute (or 5-minute) interval usage data from smart meters — the foundation for time-of-use rates, load research, and operational analytics.',
    'Customer team',
    'Meter Data Management (MDM) system',
    '15-minute intervals / daily collection'
);

SELECT update_asset_detail(
    'Meter/AMI/MDM', 'Meter Events / Alarms',
    'Meter health events including power outages, tampering, low battery, communication failures, and voltage anomalies.',
    'Customer team',
    'Meter Data Management (MDM) system',
    'Real-time / event-driven'
);

SELECT update_asset_detail(
    'Meter/AMI/MDM', 'Remote Connect/Disconnect',
    'Service switch status and remote connect/disconnect commands — automates service activation and collections.',
    'Customer team',
    'AMI Head-End System (HES)',
    'On-demand / real-time command response'
);

SELECT update_asset_detail(
    'Meter/AMI/MDM', 'Meter Register Reads',
    'Monthly register reads (kWh, kW demand) for billing cycles — validated and edited in MDM before sending to CIS.',
    'Customer team',
    'Meter Data Management (MDM) system',
    'Daily / monthly billing cycles'
);

SELECT update_asset_detail(
    'Meter/AMI/MDM', 'Meter Configuration & Firmware',
    'Meter device registry, firmware versions, configuration parameters, and over-the-air update management.',
    'Customer team',
    'Meter Data Management (MDM) system',
    'On-demand / quarterly firmware pushes'
);

SELECT update_asset_detail(
    'Meter/AMI/MDM', 'Voltage Monitoring',
    'Meter-reported voltage data for power quality analysis, CVR verification, and grid health monitoring.',
    'Customer team',
    'Meter Data Management (MDM) system',
    '15-minute intervals / daily aggregation'
);

SELECT update_asset_detail(
    'Meter/AMI/MDM', 'Revenue Protection & Loss Analysis',
    'Usage pattern anomalies, tamper detection, and consumption-vs-delivery reconciliation to identify non-technical losses.',
    'Customer team',
    'Meter Data Management (MDM) system',
    'Daily / monthly analytics'
);

-- ==============================================================================
-- CIS/Billing (8 assets) — Customer
-- ==============================================================================
SELECT update_asset_detail(
    'CIS/Billing', 'Customer Accounts',
    'Customer master records including name, address, contact information, account history, and service agreements.',
    'Customer team',
    'Customer Information System (CIS)',
    'Real-time transactional'
);

SELECT update_asset_detail(
    'CIS/Billing', 'Billing Engine / Determinants',
    'Rate calculation engine, billing determinants, proration rules, and invoice generation — produces customer bills.',
    'Customer team',
    'Customer Information System (CIS)',
    'Daily / monthly billing cycles'
);

SELECT update_asset_detail(
    'CIS/Billing', 'Rate Schedules / Tariffs',
    'Tariff structures, rate tables, time-of-use periods, demand charges, and regulatory rate riders.',
    'Customer team',
    'Customer Information System (CIS)',
    'On-demand / regulatory rate case updates'
);

SELECT update_asset_detail(
    'CIS/Billing', 'Payment Processing',
    'Payment transactions, lockbox deposits, credit card processing, bank drafts, and payment plans.',
    'Customer team',
    'Customer Information System (CIS)',
    'Real-time transactional'
);

SELECT update_asset_detail(
    'CIS/Billing', 'Collections & Credit',
    'Delinquency tracking, collection workflows, credit scores, deposit requirements, and write-offs.',
    'Customer team',
    'Customer Information System (CIS)',
    'Daily batch / real-time updates'
);

SELECT update_asset_detail(
    'CIS/Billing', 'Premise & Service Point',
    'Premise locations, service points, meter assignments, and service addresses — links physical infrastructure to customer accounts.',
    'Customer team',
    'Customer Information System (CIS)',
    'Real-time transactional'
);

SELECT update_asset_detail(
    'CIS/Billing', 'Customer Programs (DR/EE/Solar)',
    'Enrollment in demand response, energy efficiency rebates, net metering, and renewable incentive programs.',
    'Customer team',
    'Customer Information System (CIS)',
    'Daily / monthly program data'
);

SELECT update_asset_detail(
    'CIS/Billing', 'Call Center & Work Order Integration',
    'Service order management, customer inquiries, complaints, and field work order dispatch originating from CIS.',
    'Customer team',
    'Customer Information System (CIS)',
    'Real-time transactional'
);

-- ==============================================================================
-- Fuel Management (7 assets) — Generation
-- ==============================================================================
SELECT update_asset_detail(
    'Fuel Management', 'Fuel Inventory & Delivery',
    'Coal, natural gas, oil, and uranium inventory levels, deliveries, consumption tracking, and yard management.',
    'Generation team',
    'Fuel Management System',
    'Daily batch'
);

SELECT update_asset_detail(
    'Fuel Management', 'Coal Quality & Blending',
    'Coal quality testing results (BTU, ash, sulfur, moisture), blending recipes, and quality impact on generation performance.',
    'Generation team',
    'Fuel Management System',
    'Daily / per-shipment'
);

SELECT update_asset_detail(
    'Fuel Management', 'Gas Procurement & Transportation',
    'Natural gas supply contracts, pipeline transportation agreements, daily nominations, and imbalance tracking.',
    'Generation team',
    'Fuel Management System',
    'Daily / hourly gas flow data'
);

SELECT update_asset_detail(
    'Fuel Management', 'Fuel Cost Accounting',
    'Fuel purchase prices, delivered costs, burn costs, and cost allocation to generation units — feeds financial settlement and regulatory reports.',
    'Generation team',
    'Fuel Management System',
    'Daily / monthly close'
);

SELECT update_asset_detail(
    'Fuel Management', 'Fuel Forecasting & Optimization',
    'Fuel consumption forecasts, inventory optimization, and hedging strategy analysis.',
    'Generation team',
    'Fuel Management System',
    'Weekly / monthly planning cycles'
);

SELECT update_asset_detail(
    'Fuel Management', 'Environmental Compliance (Fuel)',
    'Sulfur content tracking, emissions allowances, and compliance with fuel-related environmental regulations.',
    'Regulatory team',
    'Fuel Management System',
    'Daily / monthly reporting'
);

SELECT update_asset_detail(
    'Fuel Management', 'Nuclear Fuel Cycle',
    'Uranium procurement, enrichment, fuel fabrication, core loading patterns, burnup tracking, and spent fuel management.',
    'Generation team',
    'Fuel Management System',
    'Monthly / refueling cycle (18-24 months)'
);

-- ==============================================================================
-- GIS (6 assets) — Distribution
-- ==============================================================================
SELECT update_asset_detail(
    'GIS', 'Electric Network Model / Connectivity',
    'Geospatial electric network model including poles, transformers, conductors, switches, and connectivity — the authoritative spatial asset registry.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'Real-time edits / nightly batch to ADMS'
);

SELECT update_asset_detail(
    'GIS', 'Utility Network (ArcGIS)',
    'ESRI utility network topology and tracing engine for connectivity analysis, upstream/downstream traces, and isolation planning.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'Real-time edits / nightly batch to ADMS'
);

SELECT update_asset_detail(
    'GIS', 'Asset Inventory (Spatial)',
    'Spatial inventory of all distribution assets with attributes including installation date, condition, ratings, and ownership.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'Real-time edits / nightly batch'
);

SELECT update_asset_detail(
    'GIS', 'Land Records & Rights-of-Way',
    'Parcels, easements, rights-of-way, and land ownership data — critical for permitting, planning, and compliance.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'Monthly / on-demand updates'
);

SELECT update_asset_detail(
    'GIS', 'Basemap & Imagery',
    'Aerial imagery, street maps, topography, and reference layers — provides spatial context for field and office users.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'Quarterly / annual imagery updates'
);

SELECT update_asset_detail(
    'GIS', 'Customer Service Territory',
    'Service territory boundaries, franchise areas, and jurisdiction polygons — defines regulatory and operational scope.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'Annual / regulatory updates'
);

-- ==============================================================================
-- CEMS (6 assets) — Regulatory
-- ==============================================================================
SELECT update_asset_detail(
    'CEMS', 'Gas Analyzers (NOx/SO2/CO2/O2)',
    'Continuous emissions monitoring of nitrogen oxides, sulfur dioxide, carbon dioxide, and oxygen — required for EPA Part 75 compliance.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Real-time / 1-minute averages'
);

SELECT update_asset_detail(
    'CEMS', 'Opacity / Particulate',
    'Opacity monitors and particulate matter (PM) measurements for visible emissions compliance.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Real-time / 6-minute averages'
);

SELECT update_asset_detail(
    'CEMS', 'Stack Flow',
    'Flue gas flow rate and volumetric measurements required for mass emissions calculations.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Real-time / 1-minute averages'
);

SELECT update_asset_detail(
    'CEMS', 'Mercury (Hg)',
    'Continuous or sorbent-trap mercury emissions monitoring for coal-fired units under MATS regulations.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Real-time / quarterly sorbent trap'
);

SELECT update_asset_detail(
    'CEMS', 'CO (Carbon Monoxide)',
    'Carbon monoxide monitoring for combustion efficiency and NAAQS compliance.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Real-time / 1-minute averages'
);

SELECT update_asset_detail(
    'CEMS', 'Data Acquisition & Reporting (DAHS)',
    'CEMS data acquisition and handling system (DAHS) — validates, archives, and reports emissions data to EPA and state agencies.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Hourly / quarterly reports'
);

-- ==============================================================================
-- Radiation/Dosimetry (6 assets) — Regulatory (Nuclear)
-- ==============================================================================
SELECT update_asset_detail(
    'Radiation/Dosimetry', 'Area Radiation Monitors',
    'Fixed radiation detectors in plant areas monitoring gamma dose rates — alarms and access control integration.',
    'Regulatory team',
    'Radiation Monitoring System',
    'Real-time / continuous'
);

SELECT update_asset_detail(
    'Radiation/Dosimetry', 'Personnel Dosimetry',
    'Electronic dosimeters, TLD badges, and whole-body dose tracking for radiation workers — required for NRC occupational dose limits.',
    'Regulatory team',
    'Radiation Protection System',
    'Daily / monthly dose reports'
);

SELECT update_asset_detail(
    'Radiation/Dosimetry', 'Contamination Monitoring',
    'Portal monitors, friskers, and hand/foot contamination detectors — prevents spread of radioactive contamination.',
    'Regulatory team',
    'Radiation Monitoring System',
    'Real-time / per-survey'
);

SELECT update_asset_detail(
    'Radiation/Dosimetry', 'Effluent Monitoring (Radwaste)',
    'Liquid and gaseous radioactive effluent monitors for discharge pathways — ensures releases are within NRC limits.',
    'Regulatory team',
    'Radiation Monitoring System',
    'Real-time / hourly averages'
);

SELECT update_asset_detail(
    'Radiation/Dosimetry', 'Environmental Monitoring (Nuclear)',
    'Off-site environmental radiation sampling including air, water, soil, and vegetation — demonstrates no impact to public.',
    'Regulatory team',
    'Radiation Protection System',
    'Weekly / quarterly sampling'
);

SELECT update_asset_detail(
    'Radiation/Dosimetry', 'Dose Assessment & ALARA',
    'Cumulative dose tracking, ALARA (As Low As Reasonably Achievable) planning, and exposure trending for regulatory reporting.',
    'Regulatory team',
    'Radiation Protection System',
    'Daily / monthly dose reports'
);

-- ==============================================================================
-- Weather (6 assets) — Generation & Distribution
-- ==============================================================================
SELECT update_asset_detail(
    'Weather', 'NWP Forecasts',
    'Numerical weather prediction (NWP) model outputs including temperature, wind, precipitation, and cloud cover — drives load and renewable forecasts.',
    'Generation team',
    'Weather data service (e.g., AWS, DTN, Weather Underground)',
    'Hourly forecasts / updated every 6 hours'
);

SELECT update_asset_detail(
    'Weather', 'Site-Specific Forecasts',
    'Hyper-local forecasts for generation sites, substations, and service territory — optimized for utility-specific locations.',
    'Generation team',
    'Weather data service',
    'Hourly / updated every 3 hours'
);

SELECT update_asset_detail(
    'Weather', 'Lightning Detection',
    'Real-time lightning strike locations and intensities — used for storm tracking, outage prediction, and crew safety.',
    'Distribution team',
    'Lightning detection network',
    'Real-time / sub-second'
);

SELECT update_asset_detail(
    'Weather', 'Radar & Satellite Imagery',
    'Weather radar and satellite imagery for storm visualization and operational situational awareness.',
    'Distribution team',
    'Weather data service',
    '5-minute to 15-minute updates'
);

SELECT update_asset_detail(
    'Weather', 'Historical Weather (Actuals)',
    'Observed weather conditions including temperature, humidity, wind, and precipitation — used for model training and load research.',
    'Generation team',
    'Weather data service',
    'Hourly / daily'
);

SELECT update_asset_detail(
    'Weather', 'Severe Weather Alerts',
    'NWS watches, warnings, and advisories — triggers storm preparedness and outage response protocols.',
    'Distribution team',
    'Weather data service',
    'Real-time / event-driven'
);

-- ==============================================================================
-- DERMS (5 assets) — Distribution
-- ==============================================================================
SELECT update_asset_detail(
    'DERMS', 'DER Aggregation & Control',
    'Aggregated control of distributed energy resources (solar, storage, EV chargers) for grid services and demand response.',
    'Distribution team',
    'Distributed Energy Resource Management System (DERMS)',
    'Real-time / 1-minute to 5-minute dispatch'
);

SELECT update_asset_detail(
    'DERMS', 'Solar PV Monitoring',
    'Real-time monitoring of behind-the-meter and utility-scale solar PV generation for visibility and forecast reconciliation.',
    'Distribution team',
    'Distributed Energy Resource Management System (DERMS)',
    'Real-time / 1-minute to 5-minute'
);

SELECT update_asset_detail(
    'DERMS', 'Battery Storage Dispatch',
    'State of charge, charge/discharge commands, and economic dispatch of distributed battery storage.',
    'Distribution team',
    'Distributed Energy Resource Management System (DERMS)',
    'Real-time / 1-minute'
);

SELECT update_asset_detail(
    'DERMS', 'EV Charging Management',
    'Managed charging schedules, load curtailment, and V2G (vehicle-to-grid) coordination for electric vehicle charging infrastructure.',
    'Distribution team',
    'Distributed Energy Resource Management System (DERMS)',
    'Real-time / 5-minute to 15-minute'
);

SELECT update_asset_detail(
    'DERMS', 'DER Forecasting',
    'Forecasts of behind-the-meter solar, storage, and EV load for distribution planning and operational visibility.',
    'Distribution team',
    'Distributed Energy Resource Management System (DERMS)',
    'Hourly updates / 5-minute actuals'
);

-- ==============================================================================
-- PMU/Synchrophasor (5 assets) — Transmission
-- ==============================================================================
SELECT update_asset_detail(
    'PMU/Synchrophasor', 'Phasor Measurement Units (PMUs)',
    'High-speed voltage and current phasor measurements with GPS time-sync — enables wide-area monitoring and disturbance analysis.',
    'Transmission team',
    'Phasor Data Concentrator (PDC)',
    'Real-time / 30 samples per second'
);

SELECT update_asset_detail(
    'PMU/Synchrophasor', 'Phasor Data Concentrator',
    'Aggregation, time-alignment, and storage of PMU data from across the transmission system.',
    'Transmission team',
    'Phasor Data Concentrator (PDC)',
    'Real-time / continuous streaming'
);

SELECT update_asset_detail(
    'PMU/Synchrophasor', 'Oscillation Detection & Damping',
    'Real-time detection of inter-area oscillations and automated damping control to prevent instability.',
    'Transmission team',
    'Wide-Area Monitoring System (WAMS)',
    'Real-time / sub-second'
);

SELECT update_asset_detail(
    'PMU/Synchrophasor', 'Disturbance Recording & Playback',
    'High-resolution capture of faults, relay operations, and system disturbances for root cause analysis.',
    'Transmission team',
    'Phasor Data Concentrator (PDC)',
    'Event-triggered / archived'
);

SELECT update_asset_detail(
    'PMU/Synchrophasor', 'Voltage Stability Monitoring',
    'Real-time voltage stability indices and early warning of voltage collapse conditions.',
    'Transmission team',
    'Wide-Area Monitoring System (WAMS)',
    'Real-time / 1-second to 10-second'
);

-- ==============================================================================
-- Document/Content Mgmt (5 assets) — Corporate Services
-- ==============================================================================
SELECT update_asset_detail(
    'Document/Content Mgmt', 'Engineering Documents & Drawings',
    'Controlled engineering documents, as-built drawings, P&IDs, one-lines, and design specifications — lifecycle-managed and revision-controlled.',
    'Corporate Services team',
    'Document Management System (DMS)',
    'On-demand / version-controlled'
);

SELECT update_asset_detail(
    'Document/Content Mgmt', 'Regulatory Filings & Reports',
    'NRC, FERC, EPA, NERC, and state regulatory submissions, permits, and compliance reports.',
    'Regulatory team',
    'Document Management System (DMS)',
    'On-demand / quarterly or annual submissions'
);

SELECT update_asset_detail(
    'Document/Content Mgmt', 'Procedures & Work Instructions',
    'Operating procedures, maintenance instructions, safety protocols, and emergency response plans — auditable and training-integrated.',
    'Corporate Services team',
    'Document Management System (DMS)',
    'On-demand / revision-controlled'
);

SELECT update_asset_detail(
    'Document/Content Mgmt', 'Vendor Documentation',
    'Equipment manuals, vendor technical specifications, warranty documents, and service bulletins.',
    'Corporate Services team',
    'Document Management System (DMS)',
    'On-demand / lifecycle-managed'
);

SELECT update_asset_detail(
    'Document/Content Mgmt', 'Contract & Legal Documents',
    'Power purchase agreements (PPAs), service contracts, interconnection agreements, and legal contracts — searchable and retention-managed.',
    'Corporate Services team',
    'Document Management System (DMS)',
    'On-demand / retention-policy-driven'
);

-- ==============================================================================
-- LIMS (4 assets) — Generation & Regulatory
-- ==============================================================================
SELECT update_asset_detail(
    'LIMS', 'Water Chemistry Testing',
    'Laboratory analysis results for boiler water, cooling water, and environmental samples — ensures chemistry control and compliance.',
    'Generation team',
    'Laboratory Information Management System (LIMS)',
    'Daily / per-sample'
);

SELECT update_asset_detail(
    'LIMS', 'Oil & Fluid Analysis',
    'Oil quality testing for transformers, turbines, and hydraulic systems — predictive indicator for equipment health.',
    'Generation team',
    'Laboratory Information Management System (LIMS)',
    'Monthly / quarterly sampling'
);

SELECT update_asset_detail(
    'LIMS', 'Environmental Sampling',
    'Water discharge, soil, air quality, and effluent testing for environmental permit compliance.',
    'Regulatory team',
    'Laboratory Information Management System (LIMS)',
    'Weekly / quarterly sampling'
);

SELECT update_asset_detail(
    'LIMS', 'Material Testing & Metallurgy',
    'Material properties, weld testing, corrosion analysis, and metallurgical failure investigations.',
    'Generation team',
    'Laboratory Information Management System (LIMS)',
    'On-demand / per-inspection'
);

-- ==============================================================================
-- Standalone SCADA (1 asset) — Generation
-- ==============================================================================
SELECT update_asset_detail(
    'Standalone SCADA', 'Plant SCADA',
    'Standalone supervisory control and data acquisition for power plants without integration to data historian — legacy systems or isolated facilities.',
    'Generation team',
    'Plant SCADA system',
    'Real-time / 2-second to 10-second scan'
);

-- ==============================================================================
-- Standalone OMS (1 asset) — Distribution
-- ==============================================================================
SELECT update_asset_detail(
    'Standalone OMS', 'Outage Management System',
    'Standalone outage management system for customer outage calls, crew dispatch, and restoration tracking — not integrated with ADMS.',
    'Distribution team',
    'Outage Management System (OMS)',
    'Real-time / transactional'
);

-- Drop helper function
DROP FUNCTION IF EXISTS update_asset_detail;

-- Report: Log population summary
DO $$
DECLARE
    populated_provides INT;
    populated_steward INT;
    populated_source_of_record INT;
    populated_refresh_cadence INT;
BEGIN
    SELECT COUNT(*) INTO populated_provides FROM data_assets WHERE provides IS NOT NULL AND provides != '';
    SELECT COUNT(*) INTO populated_steward FROM data_assets WHERE steward IS NOT NULL AND steward != '';
    SELECT COUNT(*) INTO populated_source_of_record FROM data_assets WHERE source_of_record IS NOT NULL AND source_of_record != '';
    SELECT COUNT(*) INTO populated_refresh_cadence FROM data_assets WHERE refresh_cadence IS NOT NULL AND refresh_cadence != '';
    
    RAISE NOTICE 'Asset detail content population complete:';
    RAISE NOTICE '  provides: % assets', populated_provides;
    RAISE NOTICE '  steward: % assets', populated_steward;
    RAISE NOTICE '  source_of_record: % assets', populated_source_of_record;
    RAISE NOTICE '  refresh_cadence: % assets', populated_refresh_cadence;
END $$;
