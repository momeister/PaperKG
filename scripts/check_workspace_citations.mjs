/** Read-only browser check of a saved structured answer against its PDF page. */
import { createRequire } from 'node:module';
import fs from 'node:fs';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const [answerPath, outputPath='/tmp/workspace-citation-check.json'] = process.argv.slice(2);
const raw = JSON.parse(fs.readFileSync(answerPath,'utf8'));
const answer = typeof raw.answer === 'object' ? raw.answer : raw;
const project = 'Haptic Interaction';
const verification = answer.source_verification.sources;
const block = {id:'browser-check-block',question:answer.question,answer,verification,createdAt:new Date().toISOString()};
const session = {activeTurnId:'browser-check-turn',history:[{id:'browser-check-turn',question:answer.question,answer,verification,blocks:[block],createdAt:new Date().toISOString()}]};
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[], checks=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const req=route.request();const path=new URL(req.url()).pathname;
    if (path.includes('claim-check')) checks.push(path);
    if (path.includes('/workspace/sessions/')) return route.fulfill({contentType:'application/json',body:JSON.stringify({project_id:project,payload:session})});
    if (!['GET','HEAD'].includes(req.method())) return route.fulfill({contentType:'application/json',body:'{}'});
    return route.continue();
  });
  await page.addInitScript(({project,session})=>{
    window.__API_BASE__='http://127.0.0.1:42849';
    localStorage.setItem('sciencekg.project',project);
    localStorage.setItem('sciencekg.assistant.session.'+project,JSON.stringify(session));
    localStorage.setItem('sciencekg.workspace.mode.'+project,'research');
  },{project,session});
  await page.goto('http://127.0.0.1:5173/#/workspace');
  const chip=page.locator('.answer-text .citation-link--mapped').first();
  await chip.waitFor({timeout:30000});
  const link=answer.citation_links[0];
  const evidence=answer.evidence.find(e=>e.evidence_id===link.evidence_id);
  const expectedPage=evidence.metadata.page;
  await chip.click();
  const pane=page.locator('[data-panel-id="ws-pdf"] .pdf-pane');
  await pane.locator('.pdf-page').nth(expectedPage-1).waitFor({timeout:30000});
  await page.waitForTimeout(1800);
  const position=await pane.locator('.pdf-page').nth(expectedPage-1).evaluate(el=>{
    const p=el.closest('.pdf-canvas-wrap') || el.parentElement;
    const box=el.getBoundingClientRect(), viewport=p.getBoundingClientRect();
    return {top:box.top,bottom:box.bottom,viewportTop:viewport.top,viewportBottom:viewport.bottom};
  });
  const highlightVisible = await pane.locator('.pdf-page').nth(expectedPage-1).evaluate(el => {
    const viewport=el.closest('.pdf-canvas-wrap').getBoundingClientRect();
    return [...el.querySelectorAll('.pdf-highlight--active')].some(box => {const r=box.getBoundingClientRect();return r.bottom>viewport.top&&r.top<viewport.bottom;});
  });
  const result={highlight_visible:highlightVisible,paper_id:link.paper_id,evidence_id:link.evidence_id,expected_page:expectedPage,position,errors,automatic_checks:checks,claim_context_matches:link.context===answer.claims.find(c=>c.claim_id===link.claim_id)?.text};
  await page.screenshot({path:outputPath.replace(/\.json$/,'.png')});
  fs.writeFileSync(outputPath,JSON.stringify(result,null,2));console.log(result);
  if(errors.length||checks.length||!highlightVisible||!result.claim_context_matches||position.bottom<position.viewportTop||position.top>position.viewportBottom) process.exitCode=1;
} finally {await browser.close();}
