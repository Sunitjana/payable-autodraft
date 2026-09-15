##Payable Auto-Draft

## What I eventually understood about the documents

When I started, I thought this was mainly an OCR and field-extraction problem: read the PDF and find the invoice number, date, supplier, tax, and total.

After testing the documents, I understood that the harder problem is **document understanding**.

The documents do not follow one template. Some are scanned, some have native PDF text, layouts differ, labels can be different or multilingual, and a page can contain several kinds of information. There can also be many numbers on the same page, so finding a number is not enough. The system has to understand **what that number represents and where it belongs in the payable structure**.

I also learned that the ERP total is not enough to prove an answer is correct. The ERP can recompute the same gross from different structures. A payable therefore needs to preserve the document's structure: line-level taxes should stay at line level, header taxes should stay at header level, and discounts/charges should remain separate when the document provides them.

The most important lesson was that **internal consistency is not the same as source correctness**. During testing I found cases where extracted numbers could make the arithmetic work even though the extracted value did not accurately represent the source document. That changed how I think about validation: validation should not simply ask “does the math work?” but also “is there evidence in the document for this value?”

That was the main shift in my understanding.

---

## What happens when the system sees an unfamiliar document?

I did not want the system to work by adding a special rule for every document that failed. That can fit the open examples but is unlikely to work on a held-back document with a slightly different layout.

Instead, the pipeline uses the same general process for an unfamiliar document:

```text
PDF
 ↓
Native text / OCR
 ↓
Document classification
 ↓
Payable detection
 ↓
Document grouping/splitting
 ↓
Field and line-item extraction
 ↓
Master-data matching
 ↓
Financial validation
 ↓
Evidence / confidence checks
 ↓
ERP + schema validation
 ↓
ACCEPTED or REVIEW/DECLINED
```

The OCR layer can use native PDF text when it is usable and fall back to OCR when necessary. Heavier OCR/VLM components are optional because I wanted the project to remain practical on a local CPU-based setup.

For extraction, I use labels, nearby context, patterns, and document structure rather than assuming that every supplier uses the same coordinates or exact wording.

For master data, the system matches supplier, tax, PO, payment-term and other codes against the supplied reference data. I do **not** want the system to invent a code just because it would make the record complete. If there is no reliable match, leaving it unresolved and sending the case for review is safer.

For financial validation, the system checks relationships between the extracted components and the gross. But this is only one check, not proof by itself.

### Why this can generalise

The generalisation does not come from knowing every possible invoice layout.

It comes from applying the same reasoning to different evidence:

- identify the type of document
- find invoice/payable evidence
- interpret fields using context
- preserve the document's structure
- match known values against reference data
- independently validate the result
- stop automatic processing when evidence is insufficient

So if a new document has a different layout, the system can still attempt the same process. If it has information the current extraction logic cannot interpret reliably, the expected behaviour is **REVIEW**, not a guessed value.

I think that is more realistic than claiming the system can automatically solve every unseen document.

---

## Was there a document that could not be solved in the same way?

Yes. During testing, **INV-27** was a useful example.

The document is visibly invoice-like and contains information such as an amount due, but the initial classification did not have enough invoice evidence. It was classified as `unknown`, so the normal payable path could not safely process it.

I would not describe INV-27 as “impossible to solve.” That would be too strong. The more accurate conclusion is:

> **The current version of my system did not have enough reliable evidence to automatically solve it.**

That distinction matters because the document may become solvable after improving the generic document-understanding layer or using stronger visual/LLM assistance.

I also found other difficult cases during testing where the system could extract values but the result was not trustworthy enough to accept automatically. For example, some extracted financial fields could pass an arithmetic check while still being wrong compared with the source. That showed me that simply making the validator pass is not a good solution.

So when a document asks for information that the page does not provide, or when the available evidence conflicts, the system should not manufacture an answer. It should expose the uncertainty through `REVIEW` or `DECLINED`, depending on the document decision.

---

My approach was iterative:

1. Build a basic end-to-end pipeline.
2. Run it on the documents.
3. Inspect failures rather than only looking at the final score.
4. Determine which layer failed — OCR, classification, extraction, matching, financial validation, or output validation.
5. Fix the underlying general problem where possible.
6. Re-run the full set to check that the fix did not break documents that were already working.

I deliberately avoid turning every failure into a filename-specific condition such as:

```python
if filename == "INV-27":
    ...
```

That may improve one example but does not demonstrate that the system understands the problem.

A better fix is one that has a reason independent of the filename. For example, if an invoice number is missing, the system can look for invoice identity evidence using labels, context, and structure. If the evidence remains weak, it should stop rather than guess.

This also makes the system easier to explain and maintain.

---

I do not consider the current system perfect.

The main limitations I found are:

- difficult layouts can still cause classification or extraction failures
- OCR can read text incorrectly even when it appears readable to a human
- a mathematically consistent extraction can still be source-incorrect
- some currencies or fields can be ambiguous when the document contains unrelated financial information
- line-item structure can be difficult to recover from heavily distorted tables
- optional VLM/LLM assistance improves the available tools but does not remove the need for validation

Because of these limitations, I consider `REVIEW` an important part of the design rather than a failure of the system.

---

## Why this design

The goal is not to produce the largest possible number of `ACCEPTED` documents.

The goal is to produce **bookable records that are supported by the source document and valid for the downstream ERP**.

The design therefore follows:

```text
Read
 ↓
Understand
 ↓
Extract
 ↓
Match
 ↓
Validate
 ↓
Verify
 ↓
Decide
```

rather than:

```text
Read
 ↓
Guess
 ↓
Accept
```

This is also why I focus on evaluation and error analysis, not just whether the program runs. For a document-intelligence system, I want to know **which fields were wrong, why they were wrong, and whether a change generalises to documents that were not used to create the rule**.


