# do good talent: installing and first use

do good talent suggests contractors for Canadian contract jobs. You give it a list of contractors and some job postings. It asks Google's Gemini AI which contractors fit each job, and shows you the suggestions to approve or reject. **It only suggests: you make every decision.**

You need:
- the install file, sent to you or downloaded from the project's GitHub **Releases** page (under "Assets"): **`dogoodtalent-mac-intel.dmg`** (Mac) or **`dogoodtalent-windows.zip`** (Windows);
- a Google account, to get a free Gemini key (step 3);
- your contractor file (`.xlsx` or `.csv`) and a jobs file (step 5).

---

## 1. Install

### Mac
1. Double-click **`dogoodtalent-mac-intel.dmg`**. A window opens with **dogoodtalent** and an **Applications** folder.
2. Drag **dogoodtalent** onto **Applications**.
3. Close the window, then eject the "do good talent" disk in the Finder sidebar.

### Windows
1. Right-click **`dogoodtalent-windows.zip`** and choose **Extract All…**. Pick a place you'll remember, such as your **Documents** folder.
2. Open the extracted **dogoodtalent** folder. Keep every file in it together: the app needs all of them.
3. Optional: right-click **dogoodtalent.exe**, choose **Send to › Desktop (create shortcut)**.

## 2. Open it the first time

do good talent isn't registered with Apple or Microsoft, so the first time your computer asks you to confirm. This happens only once.

### Mac
1. Open **Applications** and double-click **dogoodtalent**. A message says it can't be opened, or that Apple can't check it. Click **Done** (or **OK**).
2. Open **System Settings › Privacy & Security**. Scroll down to the message about dogoodtalent and click **Open Anyway**. Enter your Mac password if asked.
3. Click **Open** in the next message. From now on, the app opens normally.

On older macOS versions, you can instead right-click **dogoodtalent** in Applications, choose **Open**, then **Open** again.

### Windows
1. Double-click **dogoodtalent.exe**.
2. If a blue box says **"Windows protected your PC"**, click **More info**, then **Run anyway**.

The first start can take a few seconds.

## 3. Add your Gemini key (once)

1. In a web browser, go to **aistudio.google.com** and sign in with your Google account.
2. Click **Get API key**, then **Create API key**. Copy the key (a long line of letters and numbers).
3. In do good talent, click **Settings** (top right). Paste the key under **Gemini key** and click **Save key**.
4. In **Your name**, type your name and click **Save name**. It's stored with each decision you make.

Use the free tier: don't turn on billing in Google's site. The key stays on your computer; the app never shows it again.

## 4. Find matches

1. Click **Find matches** (top right).
2. **Contractors:** the first time, and whenever people are added or changed, click **Browse…** under **1. Contractors** and pick the contractor file. New people are added and changed ones updated.
3. **Jobs:** click **Browse…** under **2. Jobs** and pick a jobs file:
   - a text file (`.txt`) with **one job link per line**, or
   - one posting saved from the browser as **.html** (File › Save Page As) or **.pdf** (Print › Save as PDF).

   You can add several files, one after the other. Jobs already imported are skipped.
4. Click the big **Find matches** button. The bar shows the progress: importing jobs, then asking Gemini about each new job. New jobs take a few seconds each.
5. If a message asks about **today's Gemini quota**, the run would use most of the free daily allowance. Click **Yes, go ahead**, or **Not now** to wait until tomorrow (the allowance resets at midnight Pacific time).
6. Click **See results**.

## 5. Review the results

Each job is a card:
- **Green title:** contractors who clearly fit (up to 3).
- **Yellow title:** no clear fit; the closest one is shown for you to check.
- **No colour:** nobody in your contractor list fits this job.

Under the title are the company, location and the result. The round button on the right opens the job posting.

For each suggested contractor:
- **Gemini's reason**, in one line. **Details** shows what they meet and miss, any warnings, and quotes from the posting and profile that back the suggestion.
- **Profile** (in Details) shows everything the contractor list holds about them.
- **Approve**, **Reject** (with a reason) or **Needs info**: your decision is saved, and the box's left edge turns green, red or yellow. Changed your mind? Click the **↺** button next to the decision to reset it.

At the bottom: **Export to Excel** saves a spreadsheet of the suggestions, and **Add jobs / new search** takes you back to Find matches.

## 6. Your data

Everything stays on your computer, in the app's own folder:
- **Mac:** your user folder › Library › Application Support › dogoodtalent
- **Windows:** `%APPDATA%\dogoodtalent` (type it into File Explorer's address bar)

**Settings › Data folder** shows the exact place. To back up, copy that folder. To move to a new computer, install the app there and copy the folder into the same place.

**The contractor list and the Excel exports hold personal information.** Keep your computer password-protected, and don't share exports outside your team. Gemini only ever receives anonymous profiles: no names, contact details, locations or rates.

## If something goes wrong

- **The window stays blank:** close the app and open it again.
- **"Add your Gemini key in Settings":** see step 3.
- **A job shows "Not judged yet":** the day's Gemini allowance ran out, or the run was stopped. Click **Find matches** again later; jobs already judged aren't asked again.
- **Anything else:** write down what you clicked and what the message said, and send it, along with the file `check.txt` from the data folder if there is one, to the person who gave you the app.
