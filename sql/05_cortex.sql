-- ============================================================================
-- Suraksha 05_cortex.sql : document stage + Cortex AISQL for intake and STR narrative.
-- Run as SURAKSHA_ADMIN (stage creation) then SURAKSHA_APP (usage).
-- Requires: SNOWFLAKE.CORTEX_USER database role, a region/model combination where the
-- chosen model is available (else enable cross-region inference):
--   ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';   -- ACCOUNTADMIN, optional
-- ============================================================================
USE ROLE SURAKSHA_ADMIN;
USE WAREHOUSE SURAKSHA_WH;
USE DATABASE SURAKSHA;
USE SCHEMA CORE;

-- Internal stage for trade documents. AI_EXTRACT on staged files needs server-side
-- encryption, and a directory table so files are listable.
CREATE STAGE IF NOT EXISTS DOCS
  DIRECTORY = (ENABLE = TRUE)
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
  COMMENT = 'Bills of lading / invoices / LCs / warehouse receipts (PDF, images)';
GRANT READ, WRITE ON STAGE DOCS TO ROLE SURAKSHA_APP;

-- Upload (from a shell):
--   snow stage copy ./samples/*.pdf @SURAKSHA.CORE.DOCS --overwrite
-- or SnowSQL/CLI:  PUT file://./samples/bl_001.pdf @SURAKSHA.CORE.DOCS AUTO_COMPRESS=FALSE;
ALTER STAGE DOCS REFRESH;
-- SELECT relative_path, size FROM DIRECTORY(@DOCS);

-- ---------------------------------------------------------------- intake: AI_EXTRACT
-- Returns {"response": {...}, "error": null}.  Keys below match ExtractedFields so
-- integrations/cortex.py can map the object straight onto the dataclass.
-- One row per document in the stage:
CREATE OR REPLACE VIEW V_DOC_EXTRACTION AS
SELECT
  d.relative_path,
  AI_EXTRACT(
    file => TO_FILE('@SURAKSHA.CORE.DOCS', d.relative_path),
    responseFormat => {
      'bl_number':          'What is the Bill of Lading number (B/L No)?',
      'vessel':             'What is the name of the vessel (ocean vessel)?',
      'voyage':             'What is the voyage number?',
      'port_of_loading':    'What is the port of loading?',
      'port_of_discharge':  'What is the port of discharge?',
      'commodity':          'What is the description of the goods / commodity?',
      'quantity':           'What is the numeric quantity of goods, without the unit?',
      'quantity_unit':      'What is the unit of the quantity (MT, TONNES or KG)?',
      'value':              'What is the total invoice or LC value, as a number without currency?',
      'currency':           'What is the currency code of the value (INR, USD, ...)?',
      'shipment_date':      'What is the shipped-on-board date, in YYYY-MM-DD format?',
      'shipper':            'Who is the shipper / seller / beneficiary?',
      'consignee':          'Who is the consignee / buyer / applicant?'
    }
  ) AS extraction
FROM DIRECTORY(@SURAKSHA.CORE.DOCS) d;
GRANT SELECT ON VIEW V_DOC_EXTRACTION TO ROLE SURAKSHA_APP;

-- Single-file example:
-- SELECT AI_EXTRACT(file => TO_FILE('@SURAKSHA.CORE.DOCS', 'bl_001.pdf'),
--                   responseFormat => {'bl_number': 'What is the B/L number?'}):response AS r;

-- Flattened projection:
-- SELECT relative_path,
--        extraction:response:bl_number::STRING AS bl_number,
--        extraction:response:quantity::FLOAT   AS quantity
-- FROM V_DOC_EXTRACTION;

-- ---------------------------------------------------------------- STR narrative: AI_COMPLETE
-- Evidence-grounded narrative. The prompt forces citation markers; the app MUST still run
-- report.validate_citations() on the output (LLM text is a draft, never trusted blindly).
-- Replace the model with one available in your region (e.g. 'claude-sonnet-4-5', 'llama3.3-70b').
CREATE OR REPLACE VIEW V_STR_NARRATIVE_PROMPT AS
SELECT
  c.request_id,
  'You are an AML analyst drafting the GROUNDS OF SUSPICION section of an FIU-IND Suspicious '
  || 'Transaction Report for a trade-finance duplicate-financing case. Use ONLY the facts listed '
  || 'below. Every sentence MUST end with one or more citation markers in square brackets taken '
  || 'verbatim from the evidence list, e.g. [rule:R_EXACT_HASH] or [consortium:E0007]. '
  || 'Do not invent facts, names, amounts or dates. Do not state guilt; say "indicates" or '
  || '"is consistent with". Maximum 180 words. Facts: '
  || 'confidence_score=' || c.score::STRING || '; band=' || c.band || '; evidence: '
  || (SELECT LISTAGG('[rule:' || e.rule_id || '] ' || e.description || ' (weight ' || e.weight::STRING || ')', '; ')
        WITHIN GROUP (ORDER BY e.rule_id)
        FROM V_RULE_EVIDENCE e WHERE e.request_id = c.request_id)
  AS prompt
FROM V_CONFIDENCE c
WHERE c.band = 'HIGH';
GRANT SELECT ON VIEW V_STR_NARRATIVE_PROMPT TO ROLE SURAKSHA_APP;

-- Generate (one request):
-- SELECT request_id, AI_COMPLETE('claude-sonnet-4-5', prompt) AS narrative
-- FROM V_STR_NARRATIVE_PROMPT WHERE request_id = 'REQ-0001';
-- Equivalent legacy form: SNOWFLAKE.CORTEX.COMPLETE('claude-sonnet-4-5', prompt)
