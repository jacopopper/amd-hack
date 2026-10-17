import json
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from step_wise import db
from step_wise.api import create_app
from step_wise.config import Settings
from step_wise.models import AssessmentInput, DocumentClaim, ClinicalDraft, Profile
from step_wise.pipeline import clinical


class ClinicalWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(data=Path(self.tmp.name), reviewer_password="fixture-testing-password",
                                 gemma_backend="http", gemma_base_url="https://router.huggingface.co/v1",
                                 gemma_provider="test-provider", hf_token="synthetic-token")
        self.client = TestClient(create_app(self.settings))
        self.client.auth = ("reviewer", "fixture-testing-password")

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def create(self):
        response = self.client.post("/api/assessments", json={
            "label": "Caso clinico fittizio", "profile": {"description": "Profilo non confermato"},
            "profile_confirmed": False, "staged_data_confirmed": True, "processing_consent": True,
            "clinical_external_processing_consent": True,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_text_citations_are_page_checked_and_profile_needs_review(self):
        inputs = AssessmentInput(label="Caso clinico fittizio", profile=Profile(description="Profilo non confermato"),
                                 profile_confirmed=False, staged_data_confirmed=True, processing_consent=True,
                                 clinical_external_processing_consent=True)
        aid = db.create_assessment(self.settings, inputs.model_dump(mode="json"))
        text = "La paziente usa il deambulatore.\fUna vecchia frattura dell'anca è risolta."
        doc_id = clinical.ingest(self.settings, aid, text.encode(), "report.txt", 1)
        with db.connect(self.settings) as conn:
            row = db.assessment(conn, aid)
            document = conn.execute("SELECT format,page_count,extracted_json FROM clinical_documents WHERE id=?", (doc_id,)).fetchone()
        self.assertFalse(json.loads(row["input_json"])["profile_confirmed"])
        self.assertEqual((document["format"], document["page_count"]), ("text", 2))
        self.assertNotIn("La paziente", json.dumps({"format": document["format"], "page_count": document["page_count"]}))
        claim = DocumentClaim(field="mobility_aid", value="deambulatore", status="current", source_document_id=doc_id,
                              page=1, quote="La paziente usa il deambulatore.")
        draft = ClinicalDraft(claims=[claim], unknown_fields=["vision"])
        docs = [{"id": doc_id, "pages": json.loads(document["extracted_json"])}]
        clinical.validate_draft(draft, docs)
        claim.quote = "La paziente usa un bastone."
        with self.assertRaises(ValueError):
            clinical.validate_draft(ClinicalDraft(claims=[claim]), docs)

    def test_profile_draft_and_explicit_confirmation_api(self):
        aid = self.create()
        text = "La paziente usa il deambulatore."
        created = self.client.post(f"/api/assessments/{aid}/clinical-documents", data={"expected_input_revision": 1},
                                   files={"file": ("report.txt", text.encode(), "text/plain")})
        doc_id = created.json()["id"]
        current = self.client.get(f"/api/assessments/{aid}").json()
        source_url = current["clinical_documents"][0]["asset_url"]
        self.assertEqual(self.client.get(source_url).content, text.encode())
        self.assertEqual(self.client.get(source_url, auth=("", "")).status_code, 401)
        self.assertFalse(current["inputs"]["clinical_external_processing_consent"])
        blocked = self.client.post(f"/api/assessments/{aid}/profile/draft", json={
            "expected_input_revision": current["input_revision"], "expected_review_revision": current["review_revision"],
        })
        self.assertEqual(blocked.status_code, 409)
        payload = {**current["inputs"], "expected_input_revision": current["input_revision"],
                   "clinical_external_processing_consent": True}
        reconsented = self.client.patch(f"/api/assessments/{aid}", json=payload)
        self.assertEqual(reconsented.status_code, 200, reconsented.text)
        current = self.client.get(f"/api/assessments/{aid}").json()
        claim = DocumentClaim(field="mobility_aid", value="deambulatore", status="current", source_document_id=doc_id,
                              page=1, quote=text, reviewer_confirmed=False)
        with patch("step_wise.pipeline.clinical.gemma.complete", return_value=(ClinicalDraft(claims=[claim]), [])):
            response = self.client.post(f"/api/assessments/{aid}/profile/draft", json={
                "expected_input_revision": current["input_revision"], "expected_review_revision": current["review_revision"],
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["claims"][0]["reviewer_confirmed"])
        current_claim = {**claim.model_dump(mode="json"), "reviewer_confirmed": True}
        response = self.client.put(f"/api/assessments/{aid}/profile/confirm", json={
            "expected_input_revision": current["input_revision"], "expected_review_revision": current["review_revision"],
            "profile": {"description": "Profilo funzionale fittizio", "mobility_aid": "deambulatore"}, "claims": [current_claim],
        })
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.client.get(f"/api/assessments/{aid}").json()
        self.assertTrue(saved["inputs"]["profile_confirmed"])
        self.assertFalse(saved["inputs"]["clinical_external_processing_consent"])
        self.assertEqual(saved["inputs"]["clinical_facts"][0]["source_document_id"], doc_id)
        corrected = self.client.put(f"/api/assessments/{aid}/profile/confirm", json={
            "expected_input_revision": saved["input_revision"], "expected_review_revision": saved["review_revision"],
            "profile": {"description": "Profilo fittizio corretto localmente", "mobility_aid": "deambulatore"},
            "claims": [current_claim],
        })
        self.assertEqual(corrected.status_code, 200, corrected.text)

    def test_scanned_or_invalid_pdfs_stop_without_ocr_guess(self):
        with self.assertRaises(ValueError):
            clinical.extract_pages(b"not a pdf", ".pdf")
        with self.assertRaises(ValueError):
            clinical.extract_pages(b"not text", ".docx")

    def test_real_text_pdf_has_numbered_sources_and_image_pdf_requires_ocr(self):
        from weasyprint import HTML

        text = "La paziente usa il deambulatore."
        pages = clinical.extract_pages(HTML(string=f"<p>{text}</p>").write_pdf(), ".pdf")
        self.assertEqual(pages[0]["page"], 1)
        self.assertIn(text, clinical.normalized_quote(pages[0]["text"]))
        scan = io.BytesIO()
        Image.new("RGB", (160, 120), "white").save(scan, "PDF")
        with self.assertRaisesRegex(ValueError, "no extractable text"):
            clinical.extract_pages(scan.getvalue(), ".pdf")

    def test_document_claims_cannot_bypass_source_checks_via_generic_intake(self):
        aid = self.create()
        current = self.client.get(f"/api/assessments/{aid}").json()
        claim = {"field": "mobility_aid", "value": "walker", "status": "current", "origin": "document",
                 "source_document_id": "another-assessment-document", "page": 1, "quote": "Uses a walker.",
                 "reviewer_confirmed": True}
        forged = {**current["inputs"], "profile_confirmed": True, "clinical_facts": [claim]}
        with self.subTest(endpoint="create"):
            self.assertEqual(self.client.post("/api/assessments", json=forged).status_code, 422)
        with self.subTest(endpoint="edit"):
            response = self.client.patch(f"/api/assessments/{aid}", json={**forged, "expected_input_revision": current["input_revision"]})
            self.assertEqual(response.status_code, 422, response.text)

    def test_profile_draft_quotes_must_come_from_minimized_excerpt(self):
        text = "Il contesto è stato raccolto. La famiglia è stata informata. Il controllo è previsto a gennaio. La paziente usa un deambulatore. Il prossimo appuntamento è in primavera."
        excerpts = clinical.minimize_documents([{"id": "doc", "pages": [{"page": 1, "text": text}]}])
        claim = DocumentClaim(field="mobility_aid", value="deambulatore", status="current", source_document_id="doc",
                              page=1, quote="La paziente usa un deambulatore.")
        clinical.validate_draft(ClinicalDraft(claims=[claim]), excerpts)
        claim.quote = "Il contesto è stato raccolto."
        with self.assertRaises(ValueError):
            clinical.validate_draft(ClinicalDraft(claims=[claim]), excerpts)

    def test_clinical_model_schema_requires_source_fields_and_cannot_self_confirm(self):
        required = ClinicalDraft.model_json_schema()["$defs"]["DocumentClaim"]["required"]
        self.assertTrue({"source_document_id", "page", "quote"}.issubset(required))
        claim = {"field": "mobility_aid", "value": "walker", "status": "current",
                 "source_document_id": "doc", "page": 1, "quote": "Uses a walker."}
        for changed in ({**claim, "reviewer_confirmed": True},
                        {key: value for key, value in claim.items() if key != "source_document_id"}):
            with self.assertRaises(ValueError):
                ClinicalDraft.model_validate({"claims": [changed]})


class SchemaMigration(unittest.TestCase):
    def test_v1_database_keeps_assessment_rows_when_upgraded(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data=Path(directory))
            conn = sqlite3.connect(settings.db_path)
            conn.executescript("CREATE TABLE schema_version(version INTEGER PRIMARY KEY CHECK(version=1)); INSERT INTO schema_version VALUES(1); CREATE TABLE assessments(id TEXT PRIMARY KEY, input_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', input_revision INTEGER NOT NULL DEFAULT 1, review_revision INTEGER NOT NULL DEFAULT 0, analysis_job_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL); INSERT INTO assessments(id,input_json,created_at,updated_at) VALUES('keep-me','{}','now','now');")
            conn.close()
            db.init(settings)
            with db.connect(settings) as upgraded:
                self.assertEqual(upgraded.execute("SELECT version FROM schema_version").fetchone()[0], 3)
                self.assertEqual(upgraded.execute("SELECT id FROM assessments").fetchone()[0], "keep-me")
                self.assertIsNotNone(upgraded.execute("SELECT name FROM sqlite_master WHERE name='clinical_documents'").fetchone())
