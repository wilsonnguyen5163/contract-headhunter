<!DOCTYPE html>
<html lang="en">
<head>
 <meta charset="utf-8">
 <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
 <title>do good talent · Desktop Matching Engine</title>
 <style>
   :root {
     --bg-purple: #5D3BA1;
     --card-white: #FEFEFD;
     --accent-magenta: #884596;
     --text-black: #1A1A1A;
     --text-grey: #6B6280;
     --border-line: #E4DFF0;

     /* Darker shade status colors for Results */
     --fit-green: #1B7A43;
     --fit-green-bg: #E3F4EB;
     --fit-yellow: #B8860B;
     --fit-yellow-bg: #FFF8E6;
     --fit-red: #A31233;
     --fit-red-bg: #FDE8EC;
   }

   * { box-sizing: border-box; margin: 0; padding: 0; }

   body {
     background-color: var(--bg-purple);
     color: var(--text-black);
     font-family: "Inter", system-ui, -apple-system, sans-serif;
     min-height: 100vh;
     position: relative;
     overflow-x: hidden;
   }

   /* Background Circles */
   .bg-deco {
     position: fixed;
     inset: 0;
     pointer-events: none;
     z-index: 0;
   }
   .bg-deco i {
     position: absolute;
     border-radius: 50%;
     border: 2px solid rgba(255, 255, 255, 0.2);
     background: rgba(136, 69, 150, 0.25);
   }

   /* Header & Animated Infinity Logo */
   header {
     position: relative;
     z-index: 2;
     display: flex;
     flex-direction: column;
     align-items: center;
     padding: 24px 20px 12px;
   }

   .brand-logo {
     font-weight: 900;
     font-size: 28px;
     color: #FFFFFF;
     letter-spacing: -0.03em;
     text-align: center;
     line-height: 1.1;
     display: inline-flex;
     flex-direction: column;
     align-items: center;
   }

   .brand-title {
     display: flex;
     align-items: center;
     gap: 4px;
   }

   .infinity-symbol {
     width: 32px;
     height: 16px;
     vertical-align: middle;
     display: inline-block;
     fill: none;
     stroke: #FFFFFF;
     stroke-width: 8px;
     stroke-linecap: round;
     stroke-linejoin: round;
     animation: swing 3s ease-in-out infinite;
     transform-origin: center;
   }

   /* 30 Degree Swinging Animation */
   @keyframes swing {
     0% { transform: rotate(0deg); }
     25% { transform: rotate(30deg); }
     50% { transform: rotate(0deg); }
     75% { transform: rotate(-30deg); }
     100% { transform: rotate(0deg); }
   }

   .brand-sub {
     font-weight: 400;
     opacity: 0.9;
     font-size: 13px;
     text-transform: lowercase;
     letter-spacing: 0.06em;
     margin-top: 2px;
   }

   /* Top Navigation Tabs */
   nav.top-nav {
     display: flex;
     justify-content: center;
     gap: 16px;
     margin: 12px 0 20px;
   }
   .nav-tab {
     background: rgba(255, 255, 255, 0.15);
     color: #FFFFFF;
     border: none;
     padding: 8px 20px;
     border-radius: 20px;
     font-weight: 700;
     font-size: 14px;
     cursor: pointer;
     transition: background 0.2s;
   }
   .nav-tab.active, .nav-tab:hover {
     background: #FFFFFF;
     color: var(--bg-purple);
   }

   /* Main Container & Rounded Cards */
   main {
     position: relative;
     z-index: 2;
     max-width: 860px;
     margin: 0 auto 40px;
     padding: 0 16px;
   }

   .card {
     background: var(--card-white);
     border-radius: 24px;
     padding: 32px;
     box-shadow: 0 20px 50px rgba(20, 8, 50, 0.35);
     margin-bottom: 24px;
   }

   h1, h2 {
     color: var(--text-black);
     font-weight: 800;
     margin-bottom: 8px;
   }
   p.subtitle {
     color: var(--text-grey);
     font-size: 15px;
     margin-bottom: 20px;
   }

   /* Form & Input Components */
   .section-box {
     border: 1.5px solid var(--border-line);
     border-radius: 16px;
     padding: 20px;
     margin-bottom: 20px;
     background: #FFFFFF;
   }
   .section-title {
     font-size: 16px;
     font-weight: 700;
     color: var(--bg-purple);
     margin-bottom: 6px;
   }

   .btn {
     display: inline-flex;
     align-items: center;
     justify-content: center;
     gap: 8px;
     font-weight: 700;
     font-size: 15px;
     border: 2px solid var(--bg-purple);
     background: var(--bg-purple);
     color: #FFFFFF;
     padding: 12px 24px;
     border-radius: 14px;
     cursor: pointer;
     transition: background 0.15s, transform 0.15s;
   }
   .btn:hover {
     background: #4A2E83;
     transform: translateY(-1deg);
   }
   .btn.transparent {
     background: rgba(93, 59, 161, 0.1);
     color: var(--bg-purple);
     border-color: transparent;
   }
   .btn.transparent:hover {
     background: rgba(93, 59, 161, 0.2);
   }

   /* Results Specific Styling */
   .result-card {
     border: 1.5px solid var(--border-line);
     border-radius: 16px;
     padding: 20px;
     margin-bottom: 16px;
     background: #FFFFFF;
   }
   .badge {
     display: inline-block;
     padding: 4px 12px;
     border-radius: 12px;
     font-size: 13px;
     font-weight: 700;
   }
   .badge-approve {
     background: var(--fit-green-bg);
     color: var(--fit-green);
   }
   .badge-needs-info {
     background: var(--fit-yellow-bg);
     color: var(--fit-yellow);
   }
   .badge-reject {
     background: var(--fit-red-bg);
     color: var(--fit-red);
   }

   .action-btn-group {
     display: flex;
     gap: 10px;
     margin-top: 14px;
   }
   .btn-approve { background: var(--fit-green); border-color: var(--fit-green); }
   .btn-reject { background: var(--fit-red); border-color: var(--fit-red); }
   .btn-info { background: var(--fit-yellow); border-color: var(--fit-yellow); }

   /* Modal Overlay ("See Results" popup) */
   .modal-overlay {
     position: fixed;
     inset: 0;
     background: rgba(20, 8, 50, 0.7);
     z-index: 100;
     display: flex;
     align-items: center;
     justify-content: center;
     padding: 20px;
   }
   .modal-card {
     background: var(--card-white);
     border-radius: 24px;
     padding: 32px;
     max-width: 480px;
     width: 100%;
     text-align: center;
     box-shadow: 0 25px 50px rgba(0,0,0,0.3);
   }

   /* Fallback Sticky Bottom Bar (when popup declined) */
   .bottom-bar {
     position: fixed;
     bottom: 20px;
     left: 50%;
     transform: translateX(-50%);
     background: var(--card-white);
     border: 2px solid var(--bg-purple);
     border-radius: 20px;
     padding: 12px 24px;
     box-shadow: 0 10px 30px rgba(0,0,0,0.25);
     z-index: 90;
     display: flex;
     align-items: center;
     gap: 16px;
   }

   .hidden { display: none !important; }
 </style>
</head>
<body>

 <!-- Decorative Background Circles -->
 <div class="bg-deco" aria-hidden="true">
   <i style="width:220px;height:220px;left:-60px;top:10%"></i>
   <i style="width:120px;height:120px;right:5%;top:5%"></i>
   <i style="width:280px;height:280px;right:-100px;bottom:15%"></i>
   <i style="width:90px;height:90px;left:10%;bottom:5%"></i>
 </div>

 <!-- Header with Swinging Infinity Logo -->
 <header>
   <div class="brand-logo">
     <div class="brand-title">
       do g<svg class="infinity-symbol" viewBox="0 0 100 50"><path d="M50 25C62 5 95 5 95 25C95 45 62 45 50 25C38 5 5 5 5 25C5 45 38 45 50 25Z"/></svg>d
     </div>
     <span class="brand-sub">a talent company</span>
   </div>
 </header>

 <!-- Navigation -->
 <nav class="top-nav">
   <button class="nav-tab active" onclick="showTab('find')">FIND MATCHES</button>
   <button class="nav-tab" onclick="showTab('results')">RESULTS</button>
   <button class="nav-tab" onclick="showTab('settings')">SETTINGS</button>
 </nav>

 <main>
   <!-- FIND MATCHES TAB -->
   <div id="tab-find" class="tab-content">
     <div class="card">
       <h2>Find Talent Matches</h2>
       <p class="subtitle">Upload contractors and job postings to scan for best fits.</p>

       <div class="section-box">
         <div class="section-title">1. Contractors (optional)</div>
         <p class="subtitle">Select your contractor CSV or text file.</p>
         <button class="btn transparent">Browse File...</button>
       </div>

       <div class="section-box">
         <div class="section-title">2. Jobs</div>
         <p class="subtitle">Select your job descriptions file (.txt, .pdf).</p>
         <button class="btn transparent">Browse File...</button>
       </div>

       <button class="btn" style="width: 100%; margin-top: 10px;" onclick="triggerScan()">🔍 FIND MATCHES</button>
     </div>
   </div>

   <!-- RESULTS TAB -->
   <div id="tab-results" class="tab-content hidden">
     <div class="card">
       <h2>Match Results</h2>
       <p class="subtitle">1 job with clear fits · 0 to check by hand</p>

       <div class="result-card">
         <div style="display:flex; justify-shadow:space-between; align-items:center; margin-bottom:8px;">
           <h3>Content Producer</h3>
           <span class="badge badge-approve">Clear Fit</span>
         </div>
         <p class="subtitle">Alexandra Rabbitte Hughes · Social Media Strategy & Community Lead (Score: 80)</p>
         <p style="font-size:14px; margin: 8px 0; color: var(--text-grey);">Matches social media strategy, execution, and community management.</p>

         <div class="action-btn-group">
           <button class="btn btn-approve">✓ Approve</button>
           <button class="btn btn-reject">✕ Reject</button>
           <button class="btn btn-info">? Needs Info</button>
         </div>
       </div>
     </div>
   </div>

   <!-- SETTINGS TAB -->
   <div id="tab-settings" class="tab-content hidden">
     <div class="card">
       <h2>Settings</h2>
       <p class="subtitle">Manage API Keys and environment settings.</p>

       <div class="section-box">
         <div class="section-title">Gemini API Key</div>
         <p class="subtitle">Paste your Google AI Studio API key below.</p>
         <button class="btn">Save Key</button>
       </div>
     </div>
   </div>
 </main>

 <!-- OVERLAY POPUP: Triggered after matching scan completes -->
 <div id="results-modal" class="modal-overlay hidden">
   <div class="modal-card">
     <h2 style="color: var(--bg-purple);">Scan Complete!</h2>
     <p class="subtitle" style="margin: 12px 0 24px;">Matches have been calculated using Gemini AI.</p>
     <div style="display: flex; gap: 12px; justify-content: center;">
       <button class="btn" onclick="acceptModal()">See Results</button>
       <button class="btn transparent" onclick="declineModal()">Decline</button>
     </div>
   </div>
 </div>

 <!-- FALLBACK STICKY BOTTOM BAR (Visible if Modal is Declined) -->
 <div id="fallback-bar" class="bottom-bar hidden">
   <span style="font-weight: 700; color: var(--bg-purple); font-size: 14px;">Results ready</span>
   <button class="btn" onclick="showTab('results')">See Results</button>
 </div>

 <script>
   function showTab(tabName) {
     document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
     document.querySelectorAll('.nav-tab').forEach(el => el.classList.remove('active'));

     document.getElementById('tab-' + tabName).classList.remove('hidden');
     event.target.classList.add('active');
   }

   function triggerScan() {
     // Simulate scan process, then pop overlay
     setTimeout(() => {
       document.getElementById('results-modal').classList.remove('hidden');
     }, 300);
   }

   function acceptModal() {
     document.getElementById('results-modal').classList.add('hidden');
     document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
     document.getElementById('tab-results').classList.remove('hidden');
   }

   function declineModal() {
     document.getElementById('results-modal').classList.add('hidden');
     document.getElementById('fallback-bar').classList.remove('hidden');
   }
 </script>
</body>
</html>
