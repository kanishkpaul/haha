# Resume and application research

*22 October 2026. Research for `haha`: how to build the resume, and the application around it, that gets the most interviews for Kanishk's target roles. Vendor blogs are labelled; most "statistics" in this space come from companies selling resume tools.*

## The answer in ten points

1. **The resume is judged requirement by requirement now, not by keyword count.** Ashby marks each recruiter-defined criterion *Meets / Does not meet / Unknown*, Workday HiredScore grades A–D by how many *basic* versus *preferred* qualifications are met, and Greenhouse Talent Matching scores weighted requirements. The winning move is making every must-have the evidence supports **explicitly verifiable in the text**, so it reads "Meets" instead of "Unknown".
2. **Auto-rejection is rare; ranking and searching are not.** About 92% of recruiters surveyed don't auto-reject on formatting or keywords. Real automatic cuts are knockout answers (work authorisation, location). Low match scores sink you down a list a human reads from the top.
3. **Literal strings still matter in two places:** the job title (recruiter title filters, and Resume-Matcher gives 15% to the exact posting title in the headline) and named tools in boolean searches. Semantic matching has made stuffing pointless, not exact wording.
4. **Where you apply matters more than polish.** In Huntr's data, people sending 11–20 applications interviewed at 9.25% per application, against 2.58% for those sending 100+. In Ashby's data, referrals reach interview about 40% of the time, cold inbound about 3%. The pipeline should filter for fit and look for a referral path before it tailors anything.
5. **Tailoring works.** Tailored resumes get about 60% more callbacks (Huntr, 1.7M applications, observational).
6. **Generic AI writing hurts; light AI editing helps.** A vendor field test (Kickresume, 204 applications) got 10% positive replies with generic AI resumes, 18% with AI-tailored ones, 31% with human-written ones. A field experiment with nearly half a million job seekers found that writing *assistance* (fixing errors and phrasing, not drafting) led to about 8% more hires. LLM screeners also prefer text from their own model family (67–82% self-preference). Keep Kanishk's voice; let the model edit, not author.
7. **Never hide text.** About 1% of 200,000 resumes contained hidden prompts (Duke/UNC, USENIX Security 2026), up roughly sevenfold in a year, and over 90% were hidden keywords. Detectors exist, parsers surface the text to recruiters, and Ashby and Greenhouse now ship fraud detection.
8. **For research-engineer roles, the strongest signal is trustworthy results:** reimplementing and benchmarking correctly, building evals first, reproducibility, tests, honest limits. Bullets should make it obvious *why the result should be trusted*. Kanishk's record is unusually rich in exactly this.
9. **Format is solved; just don't break it.** One column, standard headings, text PDF (DOCX when asked or for Workday), standard fonts, contact details in the body, no icons, real location plus a specific relocation line. One page for industry new-grad roles; two pages or a CV for research roles.
10. **Your LinkedIn profile is a second resume.** LinkedIn's Hiring Assistant sources from profiles against a 41,000-skill taxonomy, and fraud checks compare profile and resume. Keep them consistent.

## 1. How the screens actually work

### The three readers

| Reader | What it does | What wins |
|---|---|---|
| **Parser** | Turns the file into fields (name, titles, dates, skills) | Clean single-column text, standard headings, parseable dates |
| **Matcher / AI reviewer** | Checks the resume against the job's requirements; recruiters search by title and keyword | Each requirement stated explicitly with context; exact title and tool names |
| **Human** | ~7.4-second first pass, eyes on titles, employers, dates, education first | Strong entry titles, bold labels, bullets that lead with results |

### What each ATS does (2026)

| ATS | AI matching | Output | Auto-reject? | Notes |
|---|---|---|---|---|
| **Ashby** | AI-Assisted Application Review against recruiter-set must/should/nice criteria | Per criterion: Meets, Does not meet, Unknown, Skipped, each linked to resume text. No score or ranking | No; only knockout screening questions | PII redacted before the model sees it; candidates can opt out. Built-in fraud detection |
| **Greenhouse** | Talent Matching (Real Talent add-on, optional) extracts skills, titles, years, dates, companies | A match category with reasons; recruiters filter by it | No, except employer-configured rules | Opt-out candidates go to "needs manual review". Fraud-risk score from identity and metadata |
| **Workday** | HiredScore | **A**: all basic + most preferred quals; **B**: all basic; **C**: most basic; **D**: most basic missing | No; orders the list | Strictest parser: headers/footers skipped, tables read out of order |
| **Lever** | Search-dependent, stemming | No ranking | No | Blind to abbreviations, so write both forms |
| **LinkedIn Hiring Assistant** | Sources from profiles (and resumes, via ATS integrations) | Shortlist with reasons | n/a | 41k-skill taxonomy; recruiters calibrate by liking/disliking |

**Design consequence:** our screener simulation should copy these mechanics exactly. Split each job's requirements into basic and preferred, judge each one Meets / Does not meet / Unknown with a quoted line of evidence, and estimate the HiredScore grade. The target is zero "Unknown" on any requirement the evidence bank supports.

### Which ATS the target companies use (checked against their public APIs)

| Company | ATS | How verified |
|---|---|---|
| OpenAI | Ashby (808 open jobs) | Ashby posting API |
| Sarvam | Ashby | Ashby posting API |
| Anthropic | Greenhouse | Greenhouse board API |
| Google DeepMind | Not Greenhouse (Google's own system) | Greenhouse API 404 |

Ashby's web board returns HTTP 200 for any name, so only the API check counts. Check each new company the same way; career-ops and the job-description step can do it from the apply URL.

## 2. What gets interviews: the evidence

| Finding | Source | Strength |
|---|---|---|
| 92% of recruiters don't auto-reject on format/keywords/AI score | Enhancv, 25 recruiters | Small survey |
| Recruiters' first pass ~7.4 s; clear sections, bold titles and bullets hold attention; columns and clutter hurt | Ladders eye-tracking, 2018 | Small study, widely replicated advice |
| Tailored resumes: ~60% more callbacks | Huntr, 1.7M applications | Observational, vendor |
| 11–20 applications: 9.25% interview rate per application; 100+: 2.58% | Huntr Q1 2026 | Observational, vendor |
| Referrals: ~40% reach interview vs ~3% cold; 18% of hires from 1% of applications | Ashby data via Noon/Huntr | Large dataset, secondhand |
| Referral interviews 35% more likely to end in an offer | Glassdoor, 2024–25 | Large dataset |
| Writing *assistance* (editing, not drafting): ~8% more hires | Wiles, Munyikwa & Horton | Large field experiment |
| Generic AI resume 10%, tailored AI 18%, human single-column 31% positive replies | Kickresume, 204 applications | Small vendor experiment |
| LLM screeners prefer their own model's resumes 67–82% of the time; same-model applicants 23–60% more likely shortlisted | arXiv 2509.00462 (AIES 2025) | Controlled experiment |
| ~1% of resumes contain hidden prompts; >90% hidden keywords; ~7× growth | Duke/UNC, USENIX Security 2026 | Large real-world dataset |
| No credible study isolates "quantified bullets → more callbacks"; the "40% more" figures trace to vendors | Search across audit literature | Gap in the evidence |

## 3. Content rules for Kanishk's target roles

**Research engineer at an AI lab** (Anthropic, OpenAI, DeepMind, Sarvam, Cohere and similar):

- Lead with evidence of trustworthy research engineering: reimplementations benchmarked correctly, eval frameworks built first, reproducible pipelines, tests on research code, honest negative results. The Indic-cat denominator fixes, the MCW memoriser baseline and the ResonatorLM controls are exactly this signal.
- Shape the strongest bullets as a compact experiment: hypothesis, intervention, evaluation set, result. Use the Google X-Y-Z frame ("accomplished X, as measured by Y, by doing Z") for engineering bullets.
- Publications: distinguish venue and status (workshop, preprint, in preparation) and authorship position. Link Scholar or Semantic Scholar if there is a profile.
- Open source: name merged contributions (the Unify PR) and adoption signals; link a focused GitHub, with pinned repos that have READMEs and tests.
- Anthropic specifically: mission fit and writing quality are screened, so free-text answers are writing samples. Write them in Kanishk's own voice.

**Structure:**

- Industry and new-grad roles: one page. Education near the top, since graduation is June 2027. Then research/industry experience (Jadavpur, Unify), then three or four projects, then skills and two or three awards.
- Research roles, residencies and fellowships: two pages or a full CV is acceptable; education and research experience first.
- Headline: the posting's exact title when it's an honest description ("AI Research Engineer"), so title filters and Resume-Matcher's title check both hit.
- Skills: grouped, ordered by the job's priorities, both forms of each acronym ("Retrieval-Augmented Generation (RAG)"), only skills with evidence.
- Every must-have the evidence supports should appear in a bullet with context and dates, not only in the skills list. That is what turns Ashby's "Unknown" into "Meets".

**Location and authorisation** (Kolkata-based, applying internationally):

- Keep the real location ("Kolkata, India"). Add a specific line such as "Open to relocation: San Francisco, London" rather than a vague "willing to relocate". Never invent a local address.
- State work-authorisation needs plainly in the portal's questions; that is where knockouts happen.
- Leave relocation language off fully remote roles.

## 4. Format and file rules

- Single column; standard headings (Experience, Education, Skills, Projects); no tables, text boxes, icons, photos or skill bars; contact details in the body, not a page header.
- Text-based PDF by default; DOCX when the portal asks, or as the Workday copy. Check that text can be selected and copied in reading order.
- Standard fonts (Arial, Calibri, Georgia, Helvetica and similar) at 10.5–12 pt.
- LaTeX: map glyphs to Unicode and avoid ligature corruption ("fi" extracting as garbage). Typst templates aimed at ATS (`basic-resume`, `signal-resume`, `simple-technical-resume`) exist. Verify every render with a text extraction.
- One date format throughout ("Aug 2026 – Present"). Resume-Matcher scores date consistency, and parsers miscount experience when formats mix.
- File name `Kanishk_Paul_Resume.pdf` or with the company; no "final(2)".
- Plain-text URLs that read correctly even when the link is stripped.

## 5. Things never to do

- **Hidden text or prompts** of any kind. Detected, surfaced to recruiters, and treated as fraud.
- **Keyword stuffing.** Semantic matchers make it useless and it reads badly to humans.
- **A resume that disagrees with LinkedIn** on titles, dates or employers. Fraud checks look for exactly this mismatch.
- **Mass-applying.** Per-application interview rates fall sharply with volume, and repeated applications to one company lower them further.
- **Generic AI prose.** It underperforms human writing with recruiters, and same-model preference makes results unpredictable across screeners.

## 6. Tools: what to adopt, borrow or skip

Stars and activity checked on GitHub on 22 Oct 2026.

| Tool | License · stars · activity | Verdict | Use for |
|---|---|---|---|
| [Resume-Matcher](https://github.com/srbhr/Resume-Matcher) | Apache-2.0 · 28.8k · today | **Adopted** (vendored) | Store, validate, score, parse-check, round-trip, render, tracker |
| [career-ops](https://github.com/career-ops-hq/career-ops) | MIT · 76.5k · today | **Adopt** | Job discovery (portal scanner preconfigured for Anthropic, OpenAI, ElevenLabs and others across Ashby, Greenhouse, Lever, Wellfound), 1–5 fit scoring that says skip below 4.0, posting-still-open and repost checks, work-authorisation blocker detection, contact discovery and outreach drafts, story bank, funnel and per-ATS advance-rate analytics. Runs inside Claude Code; never auto-submits |
| [RenderCV](https://github.com/rendercv/rendercv) | MIT · 17.8k · today | Borrow | Typst renderer if we want our own design instead of Resume-Matcher's templates |
| Typst `basic-resume`, `signal-resume` | Typst Universe | Reference | ATS-safe layout patterns |
| Greenhouse / Ashby / Lever public job APIs | Free, no key | **Adopt** | Structured job descriptions for target companies; ATS detection. Greenhouse: `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`; Ashby: `api.ashbyhq.com/posting-api/job-board/{name}`; Lever: `api.lever.co/v0/postings/{slug}?mode=json` |
| [JobSpy](https://github.com/speedyapply/JobSpy) | MIT · 4.5k · active | Optional | Indeed and Naukri discovery. LinkedIn needs proxies and may breach its terms |
| [Reactive Resume](https://github.com/AmruthPillai/Reactive-Resume) | MIT · 44.7k · today | Skip for now | Visual builder with DOCX export, if we ever want a WYSIWYG editor |
| [OpenResume](https://github.com/xitanggg/open-resume) parser | **AGPL-3.0** · 8.9k · stale since Oct 2024 | Test oracle only | Second, independent parser check. Run it unmodified; don't copy its code into ours |
| [ats-screener](https://github.com/sunnypatell/ats-screener) | MIT · 178 · active | Reference | Per-vendor strictness profiles (Workday, Taleo, iCIMS, Greenhouse, Lever, SuccessFactors) to borrow for our screener simulation. Its scores come from an LLM |
| Lightcast Open Skills | Free tier for non-profit/public-good only, 50 extractions/month | Maybe | Skill normalisation; licence terms are unclear for our use |
| ConFit v2/v3 (research) | Papers; data proprietary | Skip | Job–resume matching models; an LLM judge is enough at our scale |
| Jobscan, Rezi, Teal, Resume Worded, Enhancv | Paid, closed rubrics | Skip (one free Jobscan scan as a cross-check at most) | Their scores predict their own rubric, not a real ATS |
| Textkernel, Affinda, RChilli parsers | ~$75–99/month or trial | Optional one-off | Calibrate against a real commercial parser during a trial |
| Simplify Copilot | Free extension | Adopt for submitting | Autofills Workday, Greenhouse, Ashby, Lever forms; you click submit |
| Hosted resume MCPs (JuicedResume, ResuMakeAi) | Proprietary, hosted | Skip | They upload the resume to a third party |
| JSON Resume schema | MIT · archived | Skip | Archived; Resume-Matcher's schema and RenderCV's YAML are live |

### Customising borrowed tools later

- Fork Resume-Matcher and career-ops into `kanishkpaul/`, point the submodules at the forks, and keep `upstream` remotes so upstream fixes can still be merged in.
- Prefer their extension points over patching: Resume-Matcher's CLI/MCP tools and agent mode, career-ops' modes, plugins and `config/profile.yml`. Patches inside their code are what make upgrades painful.
- Licences: Apache-2.0 and MIT allow modification freely (keep their notices). OpenResume is AGPL-3.0, so keep it as a separate tool we run, not code we copy.

## 7. What this changes in the haha pipeline

| Change | Why |
|---|---|
| **Add a discovery and fit gate before tailoring** (career-ops): scan target companies, check the posting is live, score fit 1–5, skip below 4, flag work-authorisation blockers | Applying better to fewer roles roughly triples the per-application interview rate |
| **Ingest job descriptions from the ATS public APIs** and detect the vendor from the apply URL | Clean structured text; the vendor decides format strictness and DOCX needs |
| **Split requirements into basic and preferred** in `jobspec` | That is exactly how HiredScore grades and how Ashby criteria are written |
| **Screener simulation** in `verify`/`ats`: per-requirement Meets / Does not meet / Unknown with quoted evidence, plus an estimated A–D grade, run on two model families | Mirrors Ashby and HiredScore; running two families offsets self-preference |
| **"Unknown → Meets" rewriting:** for every supported must-have judged Unknown, make the evidence explicit in a bullet | The highest-leverage edit the evidence points to |
| **Exact posting title in the headline** when it honestly describes Kanishk | Title filters, Resume-Matcher's title score |
| **Voice guard:** cap how much of each bullet the model rewrites; seed it with Kanishk's own writing; banned-phrase list | Human-written resumes outperform generic AI; avoid same-model artefacts |
| **Referral and outreach step** (career-ops contact discovery, drafts only) | ~40% vs ~3% interview rate |
| **LinkedIn consistency check** against the tailored resume | Fraud checks; LinkedIn's AI sources from the profile |
| **Track outcomes per application** (variant, ATS, channel) with career-ops analytics | The only real ground truth; every score above is a stand-in |

## Sources

**ATS and AI screening:** [Ashby AI-assisted review in practice](https://www.ashbyhq.com/blog/recruiting/ai-assisted-application-review-in-practice) · [Ashby AI features overview (Enhancv)](https://enhancv.com/blog/how-does-ashby-work/) · [Ashby fraud detection](https://www.ashbyhq.com/blog/all/ashby-launches-the-first-ats-integrated-fraud-detection-system) · [Greenhouse Talent Matching](https://support.greenhouse.io/hc/en-us/articles/41396009937307-Talent-Matching) · [Greenhouse Real Talent](https://www.greenhouse.com/real-talent-candidate-matching) · [Workday HiredScore grades](https://doc.workday.com/hiredscore/en-us/workday-hiredscore/recruiter-productivity-/reference--candidate-grades.html) · [ATS vendor guide](https://resumegeni.com/blog/ats-system-guide-2026) · [ats-screener vendor profiles](https://github.com/sunnypatell/ats-screener) · [LinkedIn Hiring Assistant (Maginative)](https://www.maginative.com/article/linkedin-launches-ai-recruiting-agent-to-empower-recruiters/) · [Greenhouse job board API](https://developers.greenhouse.io/job-board) · [Lever/Ashby endpoints (Apify)](https://apify.com/oddsmith/ats-job-board)

**Evidence on outcomes:** [Enhancv ATS study](https://itbrief.co.uk/story/study-reveals-ats-rarely-auto-rejects-cvs-debunks-75-myth) · [Ladders eye-tracking](https://www.hrdive.com/news/eye-tracking-study-shows-recruiters-look-at-resumes-for-7-seconds/) · [Huntr: are resumes dead](https://api.huntr.co/blog/are-resumes-dead) · [Huntr Q1 2026](https://huntr.co/research/job-search-trends-q1-2026) · [Glassdoor on referrals](https://static.glassdoor.ca/blog/ai-has-not-killed-online-job-applications/) · [Referral statistics incl. Ashby data (Noon)](https://www.noon.ai/blog/articles/360-employee-referral-statistics-2026) · [Algorithmic writing assistance](https://arxiv.org/pdf/2301.08083) · [Kickresume 204-application test (StudyFinds)](https://studyfinds.com/this-resume-format-most-interviews/) · [AI self-preferencing in hiring](https://arxiv.org/abs/2509.00462) · [Hidden prompts study (Duke)](https://pratt.duke.edu/news/thwarting-prompt-injection/) · [Resume audit on new graduates](https://arxiv.org/html/2604.01933) · [LLM screening bias (Wilson & Caliskan)](https://arxiv.org/html/2407.20371v1) · [Fraud signals (DEV)](https://dev.to/yuens1002/hiring-is-a-black-box-on-both-ends-53dc)

**Content and format:** [Hiring research engineers at AI labs](https://www.recruitingfromscratch.com/blog/how-to-hire-a-research-engineer-at-an-ai-lab-2026) · [Landing roles at AI labs](https://letsdatascience.com/blog/how-to-land-a-job-at-openai-anthropic-or-google-deepmind) · [Google X-Y-Z formula (Inc.)](https://www.inc.com/bill-murphy-jr/google-recruiters-say-these-5-resume-tips-including-x-y-z-formula-will-improve-your-odds-of-getting-hired-at-google.html) · [New-grad resume length](https://www.techinterview.org/post/3233474607/engineering-resume-one-page-vs-two/) · [LaTeX and ATS](https://atsverification.com/blog/latex-resume-ats-friendly/) · [Typst resume packages](https://typst.app/universe/search/?q=resume) · [Fonts and file formats](https://jobshinobi.com/blog/ats-optimized-resume-formatting-rules-2026) · [Location and relocation lines](https://slategit.com/blog/listing-a-home-country-address-on-us-resume) · [Keywords vs semantic matching](https://atsverification.com/blog/tailor-resume-job-description-without-keyword-stuffing/) · [Cover letter statistics](https://novoresume.com/career-blog/cover-letter-statistics) · [Indian job platforms](https://faceprep.in/article/off-campus-ai-engineering-roles-for-freshers-2026-the-6-platform-map/)

**Tools:** [Resume-Matcher](https://github.com/srbhr/Resume-Matcher) · [career-ops](https://github.com/career-ops-hq/career-ops) · [RenderCV](https://github.com/rendercv/rendercv) · [Reactive Resume](https://github.com/AmruthPillai/Reactive-Resume) · [OpenResume](https://github.com/xitanggg/open-resume) · [JobSpy](https://github.com/speedyapply/JobSpy) · [Lightcast access terms](https://docs.lightcast.io/lightcast-api/docs/api-access) · [ConFit v2](https://arxiv.org/pdf/2502.12361) · [Resume tool comparison](https://dupple.com/learn/best-ai-for-resumes) · [Simplify Copilot](https://simplify.jobs/copilot) · [Grounded Optimization (hallucination defences)](https://arxiv.org/abs/2607.01457)
