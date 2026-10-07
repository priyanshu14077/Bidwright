-- =====================================================================
-- SOG RFP Intelligence Platform - Archive data model (Phase 1 / PoC)
-- PostgreSQL 16 + pgvector. Request side = what the client asked for.
-- Response side = what SOG decided and offered.
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS ref;      -- reference data (engine-controlled)
CREATE SCHEMA IF NOT EXISTS archive;  -- structured proposals
CREATE SCHEMA IF NOT EXISTS lineage;  -- source tracing and audit

-- ---------------------------------------------------------------- ref
CREATE TABLE ref.typology (code text PRIMARY KEY, label text NOT NULL);
INSERT INTO ref.typology VALUES
 ('residential','Residential'),('office','Office'),('mixed_use','Mixed use'),
 ('hospitality','Hospitality'),('masterplan','Masterplan');

CREATE TABLE ref.service (code text PRIMARY KEY, label text NOT NULL, default_area_basis text NOT NULL);
INSERT INTO ref.service VALUES
 ('architecture','Architecture','gfa'),('interior_design','Interior design','fitout_area'),
 ('landscape','Landscape','landscape_area'),('masterplan','Masterplan','site_area_ha');

CREATE TABLE ref.stage (code text PRIMARY KEY, label text NOT NULL, seq int NOT NULL);
INSERT INTO ref.stage VALUES
 ('feasibility','Feasibility',1),('concept','Concept Design',2),('schematic_design','Schematic Design',3),
 ('design_development','Design Development',4),('gfc_review','GFC Review',5),('construction_review','Construction Review',6);

-- Synonyms the normaliser maps to controlled codes (e.g. 'Scheme Design' -> schematic_design)
CREATE TABLE ref.term_synonym (
  synonym text PRIMARY KEY, target_table text NOT NULL, target_code text NOT NULL, language text DEFAULT 'en');
INSERT INTO ref.term_synonym VALUES
 ('scheme design','stage','schematic_design','en'),('anteproyecto','stage','schematic_design','es'),
 ('basic design','stage','design_development','en'),('preliminary design','stage','design_development','en'),
 ('gfc drawing review','stage','gfc_review','en'),('good for construction review','stage','gfc_review','en');

CREATE TABLE ref.studio (code text PRIMARY KEY, name text, city text, country text);
CREATE TABLE ref.location (
  location_id serial PRIMARY KEY, country text NOT NULL, city text NOT NULL,
  tier text NOT NULL CHECK (tier IN ('prime','second')), UNIQUE(country, city));
CREATE TABLE ref.fx_rate (currency char(3), per_usd numeric(14,6) NOT NULL, as_of date NOT NULL, note text,
  PRIMARY KEY (currency, as_of));
CREATE TABLE ref.escalation_index (market text, year int, annual_rate numeric(6,4), source_id int,
  PRIMARY KEY (market, year));
CREATE TABLE ref.benchmark_source (
  source_id serial PRIMARY KEY, market text, metric text, value text, unit text, year int,
  source_name text, url text, note text);
CREATE TABLE ref.qualification_rule (
  rule_id serial PRIMARY KEY, name text NOT NULL, expression jsonb NOT NULL,
  effect text NOT NULL CHECK (effect IN ('flag','recommend_no_bid')), active boolean DEFAULT true,
  owner text, updated_at timestamptz DEFAULT now());
CREATE TABLE ref.field_criticality (field text PRIMARY KEY, pricing_critical boolean NOT NULL, weight numeric(4,2));

-- ---------------------------------------------------------------- archive
CREATE TABLE archive.client (
  client_id text PRIMARY KEY, name text NOT NULL, client_type text, country text, first_engaged date);

CREATE TABLE archive.proposal (
  proposal_id text PRIMARY KEY,
  reference text UNIQUE NOT NULL,
  title text NOT NULL,
  data_origin text NOT NULL CHECK (data_origin IN ('archive','synthetic','rfp_new')),
  studio_code text REFERENCES ref.studio(code),
  client_id text REFERENCES archive.client(client_id),
  -- request side
  client_status text CHECK (client_status IN ('new','repeat')),
  location_id int REFERENCES ref.location(location_id),
  typology text REFERENCES ref.typology(code),
  sub_typology text,
  gfa_m2 numeric, site_area_m2 numeric, landscape_area_m2 numeric, fitout_area_m2 numeric,
  units int, keys int,
  complexity text CHECK (complexity IN ('low','medium','high')),
  complexity_notes text,
  -- response side
  submission_date date,
  currency char(3),
  fx_per_usd numeric(14,6),
  construction_cost_usd_m2 numeric,          -- benchmark used, if any
  fee_total_local numeric, fee_total_usd numeric,
  status text CHECK (status IN ('draft','submitted','won','lost','withdrawn')),
  outcome_date date, loss_reason text, project_challenges text,
  schema_version text DEFAULT '0.1', created_at timestamptz DEFAULT now());

CREATE TABLE archive.proposal_service (proposal_id text REFERENCES archive.proposal, service text REFERENCES ref.service,
  PRIMARY KEY (proposal_id, service));
CREATE TABLE archive.proposal_stage (proposal_id text REFERENCES archive.proposal, stage text REFERENCES ref.stage,
  PRIMARY KEY (proposal_id, stage));

CREATE TABLE archive.fee_line (
  proposal_id text REFERENCES archive.proposal, line_no int,
  service text REFERENCES ref.service, stage text REFERENCES ref.stage,
  basis text CHECK (basis IN ('pct_of_construction_cost','pct_of_fitout_cost','pct_of_landscape_cost','per_hectare','lump_sum')),
  basis_qty numeric, basis_unit text, amount_local numeric NOT NULL, amount_usd numeric,
  PRIMARY KEY (proposal_id, line_no));

CREATE TABLE archive.reimbursable (
  proposal_id text REFERENCES archive.proposal, type text CHECK (type IN ('renders','site_trip','other')),
  qty int, unit_rate_local numeric, amount_local numeric, note text, PRIMARY KEY (proposal_id, type));

CREATE TABLE archive.payment_milestone (
  proposal_id text REFERENCES archive.proposal, seq int, milestone text, pct numeric(5,2), amount_local numeric,
  PRIMARY KEY (proposal_id, seq));

CREATE TABLE archive.deliverable (proposal_id text REFERENCES archive.proposal, item text, qty int,
  PRIMARY KEY (proposal_id, item));

CREATE TABLE archive.tc_deviation (proposal_id text REFERENCES archive.proposal, clause text,
  client_request text, sog_position text, PRIMARY KEY (proposal_id, clause));

-- Proposal sections (scope text etc.) with embeddings for retrieval
CREATE TABLE archive.proposal_section (
  section_id bigserial PRIMARY KEY, proposal_id text REFERENCES archive.proposal,
  section_code text NOT NULL,           -- understanding, scope, deliverables, programme, fees, payment, exclusions, terms
  service text, stage text, body text NOT NULL, embedding vector(1536));
CREATE INDEX ON archive.proposal_section USING hnsw (embedding vector_cosine_ops);

-- Synthetic-data only: how each record was generated (drop for real archive)
CREATE TABLE archive.pricing_trace (proposal_id text REFERENCES archive.proposal, factor text, value text,
  source text, note text);

-- ---------------------------------------------------------------- lineage
CREATE TABLE lineage.source_document (
  document_id bigserial PRIMARY KEY, proposal_id text REFERENCES archive.proposal,
  channel text, file_name text, sha256 char(64) UNIQUE, blob_uri text, received_at timestamptz, doc_class text);
CREATE TABLE lineage.field_value (
  proposal_id text REFERENCES archive.proposal, field text, value text,
  document_id bigint REFERENCES lineage.source_document, page int, bbox jsonb,
  confidence numeric(4,3), origin text CHECK (origin IN ('ai','engine','human','generator')),
  status text CHECK (status IN ('proposed','confirmed','rejected')), PRIMARY KEY (proposal_id, field));
CREATE TABLE lineage.field_change (
  change_id bigserial PRIMARY KEY, proposal_id text, field text, old_value text, new_value text,
  reason text NOT NULL, changed_by text NOT NULL, changed_at timestamptz DEFAULT now());
REVOKE UPDATE, DELETE ON lineage.field_change FROM PUBLIC;   -- append-only audit
