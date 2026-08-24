-- AI Value Flywheel schema — CHUNK M: asset detail content population FIX.
-- Idempotent: safe to re-run (only updates NULL/empty values).
--
-- CORRECTS migration 018, which used the WRONG natural key (source_system, module)
-- instead of the CORRECT key (source_category, module). As a result, 105 of 146 assets
-- remain unpopulated. This migration fills those 105 assets using the correct key.
--
-- Also populates uc_requires_asset.rationale for 734 edges that remain empty.
--
-- IDEMPOTENT: uses UPDATE ... WHERE (field IS NULL OR field='') so re-running won't
-- clobber user edits. Matches assets by correct natural key (source_category, module).

-- Helper function to safely update only unpopulated fields, keyed by source_category
CREATE OR REPLACE FUNCTION update_asset_detail_by_category(
    p_source_category TEXT,
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
    WHERE source_category = p_source_category
      AND module = p_module
      AND (provides IS NULL OR provides = ''
           OR steward IS NULL OR steward = ''
           OR source_of_record IS NULL OR source_of_record = ''
           OR refresh_cadence IS NULL OR refresh_cadence = '');
END;
$$ LANGUAGE plpgsql;

-- ==============================================================================
-- ADMS (Distribution) (10 assets) — Distribution
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'D-SCADA',
    'Supervisory control and data acquisition for distribution feeders, reclosers, capacitor banks, and voltage regulators — real-time monitoring and control of distribution automation devices.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / 15-second to 1-minute'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'DMS (Distribution Management)',
    'Core distribution management functions including topology processor, load flow, and network analysis — enables advanced distribution automation and optimization.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / 1-minute to 5-minute'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'Distribution Load Forecasting',
    'Feeder-level and substation load forecasts supporting operational decisions, peak demand management, and capacity planning.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Hourly updates'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'Distribution State Estimation',
    'Real-time distribution network model solving for voltages, currents, and losses — enables operational visibility below the substation.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / 1-minute to 5-minute'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'FLISR',
    'Automated fault detection, isolation via remote switching, and service restoration to minimize outage duration and improve reliability metrics.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / sub-second detection'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'Fault Location Analysis',
    'Automated fault location algorithms using SCADA data, fault indicators, and network topology to pinpoint fault locations and reduce restoration time.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / event-driven'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'OMS (Outage Management)',
    'Outage management integrated with ADMS for predicted outage locations, crew dispatch, and estimated restoration times based on network topology.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time integration'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'Storm / Damage Assessment',
    'Predictive storm outage modeling, damage assessment tools, and resource optimization for major event response and restoration planning.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'On-demand / event-driven'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'Switching Sequence Management',
    'Planned switching procedures, clearance management, and tagging for distribution maintenance and construction — ensures safe work practices.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'On-demand / real-time updates'
);

SELECT update_asset_detail_by_category(
    'ADMS (Distribution)', 'VVO / CVR',
    'Volt-VAR optimization and conservation voltage reduction — automated control of capacitor banks and voltage regulators to reduce losses and improve power quality.',
    'Distribution team',
    'Advanced Distribution Management System (ADMS)',
    'Real-time / 1-minute to 5-minute optimization cycles'
);

-- ==============================================================================
-- CEMS (3 assets) — Regulatory
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'CEMS', 'DAHS (Data Acquisition)',
    'CEMS data acquisition and handling system (DAHS) — validates, archives, and reports emissions data to EPA and state agencies for Part 75 compliance.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Real-time / hourly and quarterly reports'
);

SELECT update_asset_detail_by_category(
    'CEMS', 'Part 75 Reporting / EDR',
    'EPA Part 75 compliance reporting and electronic data reporting (EDR) submissions for acid rain, MATS, and other federal emissions programs.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Quarterly submissions per EPA schedule'
);

SELECT update_asset_detail_by_category(
    'CEMS', 'QA/QC (RATA/Calibration)',
    'Quality assurance/quality control including relative accuracy test audits (RATA), linearity checks, and cylinder gas audits — ensures CEMS accuracy and regulatory compliance.',
    'Regulatory team',
    'Continuous Emissions Monitoring System (CEMS)',
    'Quarterly QA audits per regulatory requirements'
);

-- ==============================================================================
-- CIS/Billing (5 assets) — Customer
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'CIS/Billing', 'CRM / Contact Center',
    'Customer relationship management and contact center integration — tracks customer interactions, service requests, complaints, and inquiry resolution.',
    'Customer team',
    'Customer Information System (CIS)',
    'Real-time transactional'
);

SELECT update_asset_detail_by_category(
    'CIS/Billing', 'Payments / Collections',
    'Payment processing, lockbox deposits, credit card transactions, bank drafts, payment plans, and delinquency tracking — manages customer financial accounts.',
    'Customer team',
    'Customer Information System (CIS)',
    'Real-time transactional'
);

SELECT update_asset_detail_by_category(
    'CIS/Billing', 'Programs / DSM Enrollment',
    'Customer enrollment in demand-side management programs including demand response, energy efficiency rebates, time-of-use rates, and renewable incentives.',
    'Customer team',
    'Customer Information System (CIS)',
    'Daily / monthly program updates'
);

SELECT update_asset_detail_by_category(
    'CIS/Billing', 'Revenue Protection / Theft',
    'Usage pattern anomalies, tamper detection, theft investigations, and loss reconciliation — identifies non-technical losses and revenue leakage.',
    'Customer team',
    'Customer Information System (CIS)',
    'Daily analytics / monthly investigations'
);

SELECT update_asset_detail_by_category(
    'CIS/Billing', 'Service Orders / Field Service',
    'Service order management for connects, disconnects, meter changes, and field work — integrates CIS with field operations.',
    'Customer team',
    'Customer Information System (CIS)',
    'Real-time transactional'
);

-- ==============================================================================
-- DERMS (4 assets) — Distribution
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'DERMS', 'DER Dispatch & Control',
    'Real-time dispatch and control signals for distributed energy resources including solar, storage, and controllable loads — provides grid services and balancing.',
    'Distribution team',
    'Distributed Energy Resource Management System (DERMS)',
    'Real-time / 1-minute to 5-minute dispatch'
);

SELECT update_asset_detail_by_category(
    'DERMS', 'DER Registration & Modeling',
    'Registry of distributed energy resources with technical specifications, interconnection details, and electrical models — tracks DER portfolio and hosting capacity.',
    'Distribution team',
    'Distributed Energy Resource Management System (DERMS)',
    'Daily updates / on-demand registration'
);

SELECT update_asset_detail_by_category(
    'DERMS', 'Demand Response / Load Control',
    'Managed load curtailment and demand response program execution for peak reduction, emergency response, and economic dispatch.',
    'Customer team',
    'Distributed Energy Resource Management System (DERMS)',
    'Real-time / 5-minute to 15-minute dispatch'
);

SELECT update_asset_detail_by_category(
    'DERMS', 'Virtual Power Plant Orchestration',
    'Aggregated dispatch of heterogeneous DER portfolios as a virtual power plant — coordinates solar, storage, and load for grid services and market participation.',
    'Distribution team',
    'Distributed Energy Resource Management System (DERMS)',
    'Real-time / 5-minute dispatch intervals'
);

-- ==============================================================================
-- Data Historian (13 assets) — Generation
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Data Historian', 'Combustion / Burner Management',
    'Combustor conditions, burner tilts, fuel-air ratios, and flame stability monitoring — optimizes combustion efficiency and emissions control.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Containment',
    'Nuclear containment pressure, temperature, sump levels, and isolation valve status — critical for accident mitigation and regulatory compliance.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / sub-second to 1-second'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Emissions Process Tags',
    'Process variables correlated with emissions including excess oxygen, flue gas recirculation, and selective catalytic reduction (SCR) parameters — supports emissions optimization.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-minute'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Feedwater / Condensate',
    'Feedwater pumps, condensate polishing, deaerator levels, and feedwater heater performance — impacts unit efficiency and water chemistry.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Hydro Unit',
    'Hydro turbine wicket gate positions, water flows, head pressures, generator output, and reservoir levels — optimizes hydroelectric generation.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Pressurizer',
    'Nuclear pressurizer level, pressure, heater status, and spray valve positions — maintains reactor coolant system pressure within tight bands.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / sub-second to 1-second'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Reactor Coolant System',
    'Reactor coolant pumps, loop flows, hot and cold leg temperatures, and pressurizer parameters — core thermal-hydraulic performance monitoring.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / sub-second to 1-second'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Reactor Core / Neutronics',
    'Core neutron flux, control rod positions, axial and radial power distribution, and core thermal limits — tightly monitored for safety and operational margins.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / sub-second to 1-second'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Safety Systems (ECCS/RHR)',
    'Emergency core cooling systems, residual heat removal, safety injection, and containment spray — accident mitigation systems critical for NRC compliance.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / sub-second to 1-second'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Solar Inverter / PV',
    'Photovoltaic DC string voltages and currents, inverter AC output, module temperatures, and irradiance sensors — monitors solar array performance and anomalies.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-minute'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Steam Generator / Secondary',
    'Nuclear steam generator levels, feedwater flows, steam pressures, blowdown, and secondary-side chemistry — links reactor and turbine sides.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

SELECT update_asset_detail_by_category(
    'Data Historian', 'Vibration / Condition Monitoring',
    'Vibration sensors on rotating equipment, bearing temperatures, and condition-based alarms — enables predictive maintenance and fault detection.',
    'Generation team',
    'Data Historian (OSIsoft PI / Wonderware)',
    'Real-time / 1-second to 1-minute'
);

-- ==============================================================================
-- Document/Content Mgmt (5 assets) — Corporate Services
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Document/Content Mgmt', 'Commitment Tracking',
    'Regulatory commitments, action items from inspections and audits, and closure documentation — ensures follow-through on regulatory obligations.',
    'Regulatory team',
    'Document Management System (DMS)',
    'On-demand / monthly status reviews'
);

SELECT update_asset_detail_by_category(
    'Document/Content Mgmt', 'Engineering Drawings / CAD',
    'Controlled engineering drawings, CAD files, as-built drawings, P&IDs, one-lines, and design specifications — lifecycle-managed and revision-controlled.',
    'Corporate Services team',
    'Document Management System (DMS)',
    'On-demand / version-controlled'
);

SELECT update_asset_detail_by_category(
    'Document/Content Mgmt', 'Procedures Management',
    'Operating procedures, maintenance instructions, emergency response plans, and work instructions — auditable, training-integrated, and revision-controlled.',
    'Corporate Services team',
    'Document Management System (DMS)',
    'On-demand / revision-controlled'
);

SELECT update_asset_detail_by_category(
    'Document/Content Mgmt', 'Records Management / Retention',
    'Corporate records retention, archival, and destruction scheduling per legal and regulatory requirements — searchable repository with lifecycle management.',
    'Corporate Services team',
    'Document Management System (DMS)',
    'On-demand / retention-policy-driven'
);

SELECT update_asset_detail_by_category(
    'Document/Content Mgmt', 'Regulatory Document Mgmt',
    'NRC, FERC, EPA, NERC, and state regulatory filings, permits, compliance reports, and correspondence — auditable trail of regulatory interactions.',
    'Regulatory team',
    'Document Management System (DMS)',
    'On-demand / quarterly or annual submissions'
);

-- ==============================================================================
-- EAM/APM (11 assets) — Corporate Services
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'EAM/APM', 'Asset Performance & Health Scoring',
    'Composite asset health indices, probability of failure, consequence of failure, and risk matrices — prioritizes capital and maintenance investments.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Calibration Management',
    'Calibration schedules, test results, out-of-tolerance logs, and metrology standards for instruments and protective devices — ensures measurement accuracy.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Condition Monitoring / PdM',
    'Predictive maintenance data including vibration, thermography, oil analysis, and analytics results linked to asset records for condition-based maintenance.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Failure Analysis / RCA',
    'Failure modes, root cause analysis, corrective actions, and failure investigation reports — drives reliability improvement and lessons learned.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Inspection / Rounds',
    'Operator rounds, inspection checklists, condition observations, and photo documentation — proactive equipment monitoring and early fault detection.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Mobile Workforce Management',
    'Mobile field technician apps for work order updates, time capture, photo/sketch attachments, and offline sync — extends EAM to field crews.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Real-time / on-demand'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Reliability / RCM',
    'Reliability-centered maintenance analysis, failure mode effects analysis (FMEA), and maintenance strategy optimization — balances risk and cost.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Reliability Library / Failure Modes',
    'Standard failure mode library, equipment criticality classifications, and reliability engineering reference data — supports RCM and strategy development.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Scheduling & Crew Optimization',
    'Maintenance scheduling, crew assignment, skill tracking, and workforce optimization — maximizes labor utilization and minimizes downtime.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Spare Parts / Inventory',
    'Spare parts catalogs, storeroom inventory, reorder points, and equipment bill of materials — tightly integrated with work orders and procurement.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'EAM/APM', 'Vibration / Condition Routes',
    'Vibration data collection routes, trending, and alarm management for rotating equipment — predictive indicator for mechanical failures.',
    'Corporate Services team',
    'EAM/APM system (IBM Maximo / SAP PM / Infor EAM)',
    'Daily batch'
);

-- ==============================================================================
-- EMS (Transmission) (12 assets) — Transmission
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'AGC / LFC',
    'Automatic generation control and load-frequency control — balances generation with load in real-time and maintains scheduled interchange.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 4-second AGC cycle'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Disturbance / SOE Analysis',
    'Sequence of events (SOE) recording, disturbance analysis, and event playback for fault investigations and NERC compliance.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Event-triggered / archived'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Economic Dispatch',
    'Economic dispatch optimization allocating generation to minimize production costs while meeting load and transmission constraints.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 5-minute to 15-minute'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Load Forecasting',
    'Short-term transmission-level load forecasts (hourly, daily, weekly) for scheduling, dispatch planning, and reserve requirements.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Hourly updates'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Network Model / CIM',
    'Common Information Model (CIM) based electrical network model including substations, transmission lines, transformers, and impedances — foundation for EMS analysis.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Weekly updates / on-demand for topology changes'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Network Topology Processor',
    'Real-time network topology processor determining electrical connectivity from breaker and switch status — enables state estimation and power flow.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 30-second to 2-minute'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Optimal Power Flow',
    'Optimal power flow (OPF) calculation minimizing transmission losses and congestion costs while respecting voltage and thermal limits.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 5-minute to 15-minute'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Real-Time Contingency Analysis',
    'N-1 and N-2 contingency analysis for transmission reliability and NERC compliance — identifies potential violations and suggests corrective actions.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 5-minute to 15-minute'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'State Estimation',
    'Real-time network model solving for bus voltages, line flows, and losses across the transmission grid — foundational for all EMS applications.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 30-second to 2-minute'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'T-SCADA',
    'Transmission SCADA telemetry including breaker status, MW/MVAR flows, voltages, and frequency from substations — real-time grid visibility.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 2-second to 4-second scan'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Unit Commitment',
    'Multi-period unit commitment optimization determining generation unit start-up and shut-down schedules to minimize production costs.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Hourly / day-ahead planning'
);

SELECT update_asset_detail_by_category(
    'EMS (Transmission)', 'Voltage / VAR Scheduling',
    'Voltage and reactive power scheduling including capacitor bank control, reactor switching, and transformer tap control to maintain voltage standards.',
    'Transmission team',
    'Energy Management System (EMS)',
    'Real-time / 1-minute to 5-minute'
);

-- ==============================================================================
-- ERP (3 assets) — Corporate Services
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'ERP', 'Inventory / Warehouse',
    'Inventory management, warehouse operations, stock levels, and materials planning for spare parts, consumables, and supply chain logistics.',
    'Corporate Services team',
    'SAP ERP — Materials Management module',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'ERP', 'Procurement / Sourcing',
    'Purchase requisitions, purchase orders, vendor management, contract tracking, and procurement analytics — manages external supplier interactions.',
    'Corporate Services team',
    'SAP ERP — Materials Management / Procurement module',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'ERP', 'Work Clearance Management',
    'Work clearance requests, isolation procedures, tagging, and safety permits — ensures safe work execution and regulatory compliance.',
    'Corporate Services team',
    'SAP ERP — Plant Maintenance module',
    'Real-time transactional'
);

-- ==============================================================================
-- Fuel Management (6 assets) — Generation
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Fuel Management', 'Core Design / Reload',
    'Nuclear core design, fuel loading patterns, reload analysis, and physics calculations — ensures safe and efficient fuel utilization.',
    'Generation team',
    'Fuel Management System',
    'Per refueling cycle (18-24 months)'
);

SELECT update_asset_detail_by_category(
    'Fuel Management', 'Fossil Fuel Inventory',
    'Coal, oil, and biomass inventory levels, deliveries, consumption tracking, and yard management — critical for generation dispatch.',
    'Generation team',
    'Fuel Management System',
    'Daily batch'
);

SELECT update_asset_detail_by_category(
    'Fuel Management', 'Fuel Contracts / Procurement',
    'Fuel supply contracts, pricing, delivery schedules, and vendor performance — manages fuel procurement and cost optimization.',
    'Generation team',
    'Fuel Management System',
    'Daily / monthly contract updates'
);

SELECT update_asset_detail_by_category(
    'Fuel Management', 'Fuel Quality / Blending',
    'Fuel quality testing results (BTU, ash, sulfur, moisture), blending recipes, and quality impact on generation performance and emissions.',
    'Generation team',
    'Fuel Management System',
    'Daily / per-shipment'
);

SELECT update_asset_detail_by_category(
    'Fuel Management', 'Gas Nominations',
    'Natural gas daily nominations, pipeline transportation scheduling, and imbalance tracking — coordinates gas delivery with generation dispatch.',
    'Generation team',
    'Fuel Management System',
    'Daily / hourly gas flow data'
);

SELECT update_asset_detail_by_category(
    'Fuel Management', 'Spent Fuel Management',
    'Spent nuclear fuel tracking, pool inventory, dry cask storage, and transfer planning — manages long-term spent fuel storage.',
    'Generation team',
    'Fuel Management System',
    'Monthly / per refueling cycle'
);

-- ==============================================================================
-- GIS (3 assets) — Distribution
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'GIS', 'Mobile GIS / Field Maps',
    'Mobile GIS applications for field crews providing asset location, network connectivity, and as-built data on tablets and smartphones.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'Real-time / on-demand sync'
);

SELECT update_asset_detail_by_category(
    'GIS', 'Staking / Design',
    'Distribution design, staking sheets, construction plans, and make-ready analysis — supports new construction and system expansions.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'On-demand / per project'
);

SELECT update_asset_detail_by_category(
    'GIS', 'Vegetation Management',
    'Tree inventory, trim cycle tracking, vegetation encroachment, and forestry management — critical for reliability and outage prevention.',
    'Distribution team',
    'Geographic Information System (GIS)',
    'Annual / seasonal updates'
);

-- ==============================================================================
-- LIMS (4 assets) — Generation & Regulatory
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'LIMS', 'QA/QC / Control Charts',
    'Quality assurance/quality control charts, control limits, and out-of-control detection for laboratory testing processes — ensures analytical accuracy.',
    'Generation team',
    'Laboratory Information Management System (LIMS)',
    'Daily / per test batch'
);

SELECT update_asset_detail_by_category(
    'LIMS', 'Results Management',
    'Laboratory test results, certificates of analysis, and sample traceability — links samples to analytical data and actions.',
    'Generation team',
    'Laboratory Information Management System (LIMS)',
    'Daily / per-sample'
);

SELECT update_asset_detail_by_category(
    'LIMS', 'Sample Management',
    'Sample tracking from collection through analysis including chain of custody, sample preparation, and disposition — ensures traceability.',
    'Generation team',
    'Laboratory Information Management System (LIMS)',
    'Daily / per-sample'
);

SELECT update_asset_detail_by_category(
    'LIMS', 'Water / Fuel Chemistry',
    'Chemistry analysis results for boiler water, cooling water, fuel quality, and environmental samples — ensures chemistry control and compliance.',
    'Generation team',
    'Laboratory Information Management System (LIMS)',
    'Daily / per-sample'
);

-- ==============================================================================
-- Market/ISO Feed (9 assets) — Generation
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'Ancillary Services',
    'Market clearing prices and awards for regulation, spinning reserve, non-spinning reserve, and replacement reserves — supports ancillary services revenue.',
    'Generation team',
    'ISO/RTO market data feed',
    'Hourly / 5-minute'
);

SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'Bids & Offers',
    'Generation unit bid and offer submissions, energy curves, start-up costs, and no-load costs — defines economic participation in markets.',
    'Generation team',
    'ISO/RTO market data feed',
    'Daily / hourly updates'
);

SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'Capacity Market',
    'Capacity auction results, capacity obligations, and resource adequacy commitments — ensures availability for peak periods.',
    'Generation team',
    'ISO/RTO market data feed',
    'Annual auctions / monthly settlements'
);

SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'Congestion / Constraints',
    'Binding transmission constraints, flowgate limits, and congestion pricing — identifies transmission bottlenecks impacting dispatch.',
    'Transmission team',
    'ISO/RTO market data feed',
    'Real-time / 5-minute intervals'
);

SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'FTR / CRR',
    'Financial transmission rights (FTRs) or congestion revenue rights (CRRs) including auction results and settlement — hedges basis risk between generation and load.',
    'Generation team',
    'ISO/RTO market data feed',
    'Monthly auctions / daily settlements'
);

SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'ISO Load & Gen Forecast',
    'ISO-published system-wide and zonal load and renewable generation forecasts used for market clearing and reliability planning.',
    'Generation team',
    'ISO/RTO market data feed',
    'Hourly updates / 5-minute actuals'
);

SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'Interchange / Tie Schedules',
    'Scheduled energy transactions with neighboring utilities and ISOs, e-tags, and inadvertent interchange accounting — coordinates inter-regional flows.',
    'Transmission team',
    'ISO/RTO market data feed',
    'Real-time / hourly updates'
);

SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'Settlements / Invoicing',
    'Energy settlement statements, uplift charges, make-whole payments, and invoice line items — reconciles financial obligations.',
    'Generation team',
    'ISO/RTO market data feed',
    'Monthly settlements / weekly preliminary'
);

SELECT update_asset_detail_by_category(
    'Market/ISO Feed', 'Virtual Bids (INC/DEC)',
    'Virtual supply (INC) and virtual demand (DEC) bids for financial arbitrage between day-ahead and real-time markets — purely financial positions.',
    'Generation team',
    'ISO/RTO market data feed',
    'Daily / 5-minute settlements'
);

-- ==============================================================================
-- Meter/AMI/MDM (5 assets) — Customer
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Meter/AMI/MDM', 'Load Profiles',
    'Customer load profile curves, load research, and time-of-use analysis — supports rate design, forecasting, and customer segmentation.',
    'Customer team',
    'Meter Data Management (MDM) system',
    'Daily / monthly aggregation'
);

SELECT update_asset_detail_by_category(
    'Meter/AMI/MDM', 'MDM Repository',
    'Meter data repository storing validated interval usage, register reads, and meter events — system of record for meter data.',
    'Customer team',
    'Meter Data Management (MDM) system',
    '15-minute intervals / daily collection'
);

SELECT update_asset_detail_by_category(
    'Meter/AMI/MDM', 'Remote Disconnect / Reconnect',
    'Remote service switch control for connects, disconnects, and load limiting — automates service activation and collections.',
    'Customer team',
    'AMI Head-End System (HES)',
    'On-demand / real-time command response'
);

SELECT update_asset_detail_by_category(
    'Meter/AMI/MDM', 'VEE (Validation/Estimation/Editing)',
    'Validation, estimation, and editing of interval meter data to detect and correct anomalies before billing — ensures data quality.',
    'Customer team',
    'Meter Data Management (MDM) system',
    'Daily / monthly billing cycles'
);

SELECT update_asset_detail_by_category(
    'Meter/AMI/MDM', 'Voltage / VAR Telemetry',
    'Meter-reported voltage and reactive power data for power quality analysis, CVR verification, and grid health monitoring.',
    'Customer team',
    'Meter Data Management (MDM) system',
    '15-minute intervals / daily aggregation'
);

-- ==============================================================================
-- PMU/Synchrophasor (4 assets) — Transmission
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'PMU/Synchrophasor', 'Islanding / Angle Separation',
    'Real-time detection of system islanding and angle separation events — enables controlled separation and resynchronization.',
    'Transmission team',
    'Wide-Area Monitoring System (WAMS)',
    'Real-time / sub-second'
);

SELECT update_asset_detail_by_category(
    'PMU/Synchrophasor', 'Linear State Measurement',
    'Linear state estimation using synchrophasor measurements for improved accuracy and faster convergence than traditional SCADA-based estimation.',
    'Transmission team',
    'Wide-Area Monitoring System (WAMS)',
    'Real-time / 30 samples per second'
);

SELECT update_asset_detail_by_category(
    'PMU/Synchrophasor', 'Oscillation Detection',
    'Real-time detection of inter-area oscillations and modal analysis — early warning of instability and input to damping controls.',
    'Transmission team',
    'Wide-Area Monitoring System (WAMS)',
    'Real-time / sub-second'
);

SELECT update_asset_detail_by_category(
    'PMU/Synchrophasor', 'Phasor Measurement',
    'High-speed GPS-synchronized voltage and current phasor measurements from PMUs — enables wide-area situational awareness and disturbance analysis.',
    'Transmission team',
    'Phasor Data Concentrator (PDC)',
    'Real-time / 30 samples per second'
);

-- ==============================================================================
-- Radiation/Dosimetry (4 assets) — Regulatory (Nuclear)
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Radiation/Dosimetry', 'ALARA Planning',
    'As Low As Reasonably Achievable (ALARA) planning, dose budgets, and exposure trending for radiological work — minimizes worker dose.',
    'Regulatory team',
    'Radiation Protection System',
    'Daily / per work package'
);

SELECT update_asset_detail_by_category(
    'Radiation/Dosimetry', 'Contamination / Survey',
    'Contamination surveys, portal monitors, friskers, and hand/foot monitors — prevents spread of radioactive contamination.',
    'Regulatory team',
    'Radiation Monitoring System',
    'Real-time / per-survey'
);

SELECT update_asset_detail_by_category(
    'Radiation/Dosimetry', 'Effluent Monitoring',
    'Liquid and gaseous radioactive effluent monitors for discharge pathways — ensures releases are within NRC and EPA limits.',
    'Regulatory team',
    'Radiation Monitoring System',
    'Real-time / hourly averages'
);

SELECT update_asset_detail_by_category(
    'Radiation/Dosimetry', 'RWP / Access Control',
    'Radiation work permits (RWPs), radiological access control, and dose tracking for workers entering radiological areas — ensures safe work practices.',
    'Regulatory team',
    'Radiation Protection System',
    'Real-time / per-entry'
);

-- ==============================================================================
-- Standalone OMS (1 asset) — Distribution
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Standalone OMS', 'Outage Events & Restoration',
    'Outage events, customer calls, crew dispatch, and restoration tracking — standalone OMS for utilities without ADMS integration.',
    'Distribution team',
    'Outage Management System (OMS)',
    'Real-time transactional'
);

-- ==============================================================================
-- Standalone SCADA (1 asset) — Transmission
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Standalone SCADA', 'Real-Time Telemetry & Control',
    'Real-time SCADA telemetry and remote control for substations and generation facilities — standalone system without EMS or ADMS integration.',
    'Transmission team',
    'SCADA system',
    'Real-time / 2-second to 10-second scan'
);

-- ==============================================================================
-- Weather (3 assets) — Generation & Distribution
-- ==============================================================================
SELECT update_asset_detail_by_category(
    'Weather', 'Icing / Wind Loading',
    'Icing conditions, wind loading forecasts, and structural stress on transmission and distribution infrastructure — supports storm preparedness.',
    'Transmission team',
    'Weather data service',
    'Hourly forecasts / updated every 3 hours'
);

SELECT update_asset_detail_by_category(
    'Weather', 'Renewable Generation Forecasting',
    'Weather-based forecasts for wind and solar generation including cloud cover, wind speed, and irradiance — drives renewable dispatch.',
    'Generation team',
    'Weather data service',
    'Hourly forecasts / 5-minute updates'
);

SELECT update_asset_detail_by_category(
    'Weather', 'Storm / Severe Weather Tracking',
    'Real-time storm tracking, lightning detection, and severe weather alerts — supports outage prediction and crew safety.',
    'Distribution team',
    'Weather data service',
    'Real-time / 5-minute to 15-minute updates'
);

-- ==============================================================================
-- PART 2: Populate uc_requires_asset.rationale for 734 edges
-- ==============================================================================
-- Set-based UPDATE using source_category to generate concise, accurate rationale

UPDATE uc_requires_asset ura
SET rationale = CASE
    -- Data Historian categories: real-time operational data
    WHEN da.source_category = 'Data Historian' THEN
        'Provides real-time ' || da.module || ' operational data required for monitoring, control, and performance analysis.'
    
    -- EAM/APM: asset management and maintenance
    WHEN da.source_category = 'EAM/APM' THEN
        'Provides ' || da.module || ' data required for asset lifecycle management and maintenance optimization.'
    
    -- EMS (Transmission): transmission operations
    WHEN da.source_category = 'EMS (Transmission)' THEN
        'Provides ' || da.module || ' data required for transmission grid operations and reliability management.'
    
    -- ADMS (Distribution): distribution operations
    WHEN da.source_category = 'ADMS (Distribution)' THEN
        'Provides ' || da.module || ' data required for distribution grid operations and automation.'
    
    -- Market/ISO Feed: market operations
    WHEN da.source_category = 'Market/ISO Feed' THEN
        'Provides ' || da.module || ' market data required for generation dispatch and financial settlement.'
    
    -- Meter/AMI/MDM: customer metering
    WHEN da.source_category = 'Meter/AMI/MDM' THEN
        'Provides ' || da.module || ' meter data required for billing, usage analysis, and customer programs.'
    
    -- CIS/Billing: customer information
    WHEN da.source_category = 'CIS/Billing' THEN
        'Provides ' || da.module || ' customer data required for billing, service delivery, and account management.'
    
    -- GIS: geospatial data
    WHEN da.source_category = 'GIS' THEN
        'Provides ' || da.module || ' geospatial data required for network modeling and asset location.'
    
    -- DERMS: distributed energy resources
    WHEN da.source_category = 'DERMS' THEN
        'Provides ' || da.module || ' data required for distributed energy resource coordination and grid services.'
    
    -- PMU/Synchrophasor: wide-area monitoring
    WHEN da.source_category = 'PMU/Synchrophasor' THEN
        'Provides ' || da.module || ' synchrophasor data required for wide-area monitoring and stability analysis.'
    
    -- CEMS: emissions monitoring
    WHEN da.source_category = 'CEMS' THEN
        'Provides ' || da.module || ' emissions data required for regulatory compliance reporting.'
    
    -- LIMS: laboratory data
    WHEN da.source_category = 'LIMS' THEN
        'Provides ' || da.module || ' laboratory data required for quality control and compliance monitoring.'
    
    -- Radiation/Dosimetry: nuclear radiation monitoring
    WHEN da.source_category = 'Radiation/Dosimetry' THEN
        'Provides ' || da.module || ' radiation monitoring data required for worker safety and NRC compliance.'
    
    -- Weather: meteorological data
    WHEN da.source_category = 'Weather' THEN
        'Provides ' || da.module || ' weather data required for forecasting and operational planning.'
    
    -- Fuel Management: fuel data
    WHEN da.source_category = 'Fuel Management' THEN
        'Provides ' || da.module || ' fuel data required for generation planning and cost optimization.'
    
    -- Document/Content Mgmt: documentation
    WHEN da.source_category = 'Document/Content Mgmt' THEN
        'Provides ' || da.module || ' documentation required for regulatory compliance and operational reference.'
    
    -- ERP: enterprise resource planning
    WHEN da.source_category = 'ERP' THEN
        'Provides ' || da.module || ' data required for enterprise resource planning and financial management.'
    
    -- Standalone OMS: outage management
    WHEN da.source_category = 'Standalone OMS' THEN
        'Provides ' || da.module || ' data required for outage management and restoration coordination.'
    
    -- Standalone SCADA: SCADA telemetry
    WHEN da.source_category = 'Standalone SCADA' THEN
        'Provides ' || da.module || ' SCADA telemetry required for real-time monitoring and control.'
    
    -- Fallback for any other categories
    ELSE
        'Provides ' || da.module || ' data from ' || da.source_category || ' required for this use case.'
END
FROM data_assets da
WHERE da.id = ura.data_asset_id
  AND (ura.rationale IS NULL OR ura.rationale = '');

-- Drop helper function
DROP FUNCTION IF EXISTS update_asset_detail_by_category;

-- Report: Log population summary
DO $$
DECLARE
    populated_provides INT;
    populated_steward INT;
    populated_source_of_record INT;
    populated_refresh_cadence INT;
    populated_rationale INT;
BEGIN
    SELECT COUNT(*) INTO populated_provides FROM data_assets WHERE provides IS NOT NULL AND provides != '';
    SELECT COUNT(*) INTO populated_steward FROM data_assets WHERE steward IS NOT NULL AND steward != '';
    SELECT COUNT(*) INTO populated_source_of_record FROM data_assets WHERE source_of_record IS NOT NULL AND source_of_record != '';
    SELECT COUNT(*) INTO populated_refresh_cadence FROM data_assets WHERE refresh_cadence IS NOT NULL AND refresh_cadence != '';
    SELECT COUNT(*) INTO populated_rationale FROM uc_requires_asset WHERE rationale IS NOT NULL AND rationale != '';
    
    RAISE NOTICE 'Asset detail content FIX migration complete:';
    RAISE NOTICE '  provides: % assets', populated_provides;
    RAISE NOTICE '  steward: % assets', populated_steward;
    RAISE NOTICE '  source_of_record: % assets', populated_source_of_record;
    RAISE NOTICE '  refresh_cadence: % assets', populated_refresh_cadence;
    RAISE NOTICE '  uc_requires_asset.rationale: % edges', populated_rationale;
END $$;
