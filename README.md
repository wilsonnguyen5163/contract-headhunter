# Job Matching Assistant: Plain-Language Summary

Prepared October 14, 2026. This is an early plan, not a finished product. Costs and timings are rough estimates.

## What are we building?

A tool that watches for new **contract jobs in Canada**, checks which of **our registered contractors** fit each job, and puts the best matches in a list for a **person** to review. If the reviewer approves a match, that is the trigger for the next step (reaching out to the company).

## Why?

Contract jobs are often filled on a first-come, first-served basis. Our contractors can lose out if we find a job late. Checking job boards by hand all day is slow, so the tool does the searching and first sorting, and people make the decisions.

## How it works, step by step

1. **Collect jobs.** A teammate pastes the links of interesting postings into a simple list, and the tool reads each posting from its link. This works for most job boards and company career pages, including LinkedIn links. The tool never searches LinkedIn on its own, and never opens Indeed links: Indeed postings are copied in by hand. Automatic collection from free job-board feeds comes later.
2. **Tidy them up.** Postings are cleaned and put in one consistent format. Duplicates (the same job on several sites) are merged. Non-Canadian jobs and jobs that aren't contracts are set aside.
3. **Compare to our contractors.** The tool turns each job description and each contractor's profile into numbers that capture their meaning (the technical word is "embedding"). It then measures how close each job is to each contractor. It also checks simple rules, such as "does the contractor have the must-have skills?" and "is the language right?"
4. **Make a shortlist.** For each job it picks the few best-fit contractors and shows why: the skills that match, the skills that are missing, and a confidence level (high, medium or low).
5. **A person decides.** A reviewer opens the list and approves, rejects (with a reason) or asks for more information.

Everything runs on one computer: the client's 2019 MacBook Pro. Information is kept in simple spreadsheet-style files (CSV) that can be opened in Excel or Numbers, so there is no database to set up. Please don't edit those live files while the tool is running. No contractor information is sent to outside AI services in this first version.

## What a person does

- **Reviewer:** works through the shortlist, ideally several times a day, and records approve or reject. Each decision also teaches us how to improve the tool.
- **Whoever collects jobs:** pastes the links of promising postings into the list (one line per job), and copies in the occasional posting that can't be read from its link.
- **Someone who knows the contractors:** fills in a short, structured profile for each (specialties, skills, years of experience, seniority, languages).

## What it will *not* do (in this first version)

- It will **not send emails**. Finding the right company contact and drafting a message come later, and a person will always review and send.
- It will **not scrape LinkedIn or Indeed**, or log in to any site on our behalf.
- It will **not make final decisions**. It recommends; people decide.
- It does **not use a chatbot-style AI** for judging matches yet. We may add one later if the first results show we need it.

## What it costs

- **Software and data:** expected to be close to **$0** to start. It uses free tools, free job sources and the client's existing laptop. One thing to check: the container software we plan to use (Docker Desktop) can require a paid license for larger organizations. Some free job sources limit how many requests we can make, so we may hit those limits.
- **People's time is the real cost.** The tool should reduce it, but reviewing, filling in profiles and collecting job links still take time.
- Later steps (finding contacts, drafting emails) may add costs. We will price them before building.

## How we'll know it works

We will keep track of how many of the tool's suggestions reviewers approve, and how many good matches it missed. That tells us what to adjust. Early on, expect some wrong suggestions and some misses. The first weeks are for tuning.

## Risks to keep in mind

- **Accuracy:** matches can be wrong or missed, especially at the start. The human review step is the safety net.
- **Privacy:** contractor profiles are personal information. They stay on our computer and should not be shared outside the team. Because they are plain files, the laptop should be password-protected with disk encryption turned on.
- **Speed:** a job posted a few hours ago may already be taken, and jobs only enter the list as fast as someone adds their links.
- **Older laptop:** the 2019 MacBook must stay on, plugged in and awake for the scheduled checks, and some modern AI software no longer supports its Intel chip, so setup needs extra care. This is a technical issue we have a plan for.
- **Rules for outreach:** Canada has an anti-spam law (CASL) that affects commercial emails, even business-to-business ones. We need qualified advice before the email step. This summary is not legal advice.
- **Upkeep:** the lists of skills and "contract" wording need occasional updates.

## What we need from the team

- The contractor list with a description of each person's expertise.
- A few examples of past good and bad matches, if they exist.
- A decision on who reviews, how often, and who collects job links.
- Confirmation of which job sources we are allowed and comfortable using.

## Plan in plain terms

1. Set up the foundation and the contractor profiles.
2. Get job collection and cleanup working with a few free sources.
3. Build the matching and the review list.
4. Try it with real jobs, collect reviewer feedback and tune it.
5. Only then consider the next stage: finding the company contact and drafting emails for human review.

## Small glossary

- **Embedding:** a way of turning text into numbers so a computer can tell which texts mean similar things.
- **Skill taxonomy:** our list of skills and their alternate spellings (for example, "React" and "ReactJS" count as the same).
- **Job board source / API:** an official way for a website to share its job listings with software.
- **CASL:** Canada's Anti-Spam Legislation.
- **Prototype:** the first, simple, working version, used to learn before we invest more.
- **CSV:** a simple spreadsheet-style text file that opens in Excel or Numbers.
- **Docker container:** a sealed package of the software that runs the same way on different computers, which we use because the client's older Mac can't run some modern AI software directly.
