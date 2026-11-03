const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const VOICE_CATALOG = [
  { id: 'Voice_Clone', name: '✨ Voice Clone (ကိုယ်ပိုင်အသံ)', vip: true },
  { id: 'Nilar', name: 'နီလာ (အမျိုးသမီး)', vip: false },
  { id: 'Thiha', name: 'သီဟ (အမျိုးသား)', vip: false },
  { id: 'Ava', name: 'အေးမြတ်', vip: false },
  { id: 'Andrew', name: 'အောင်ဒင်', vip: false },
  { id: 'Emma', name: 'အိအိမွန်', vip: false },
  { id: 'Brian', name: 'ဘုန်းမြတ်', vip: false },
  { id: 'Vivienne', name: 'ဝေဝေခိုင်', vip: false },
  { id: 'Remy', name: 'ရဲရင့်အောင်', vip: false },
  { id: 'Seraphina', name: 'စန္ဒီ', vip: false },
  { id: 'Florian', name: 'ဖြိုးမင်း', vip: false },
  { id: 'Xiaoxiao', name: 'ဆုဆုလှိုင်', vip: false },
  { id: 'Giuseppe', name: 'ဂျော်နီ', vip: false },
];

let previewAudio = null;

const state = {
  projectId: (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') ? crypto.randomUUID() : Math.random().toString(36).substring(2) + Date.now().toString(36),
  video: null, videoUrl: '', dimensions: null,
  masks: [], jobId: '', dragging: null, activeTab: 'blur', isLink: false,
  uploaded: { logo: '', music: '' }, logoUrl: '', auth: null, accessToken: '', member: null,
  catalog: null, selectedPayment: null,
};

const video = $('#sourceVideo');
const stage = $('#videoStage');
const layer = $('#overlayLayer');

function displayRect() {
  const r = stage ? stage.getBoundingClientRect() : {width: 0, height: 0, left: 0, top: 0}; 
  const vw = (video && video.videoWidth) ? video.videoWidth : 9; 
  const vh = (video && video.videoHeight) ? video.videoHeight : 16;
  const scale = Math.min(r.width / vw, r.height / vh); 
  const width = vw * scale; const height = vh * scale;
  return { left: (r.width - width) / 2, top: (r.height - height) / 2, width, height };
}
function clamp(n, lower, upper) { return Math.max(lower, Math.min(upper, n)); }
function output(input, target, suffix = '') { const el = $(target); if (el && input) el.textContent = `${input.value}${suffix}`; }
function stagePoint(x, y) { const r = displayRect(); return { x: r.left + x * r.width / 100, y: r.top + y * r.height / 100 }; }
function relativePoint(x, y) { const r = displayRect(); return { x: clamp((x - r.left) / r.width, 0, 1), y: clamp((y - r.top) / r.height, 0, 1) }; }
function authHeaders() { return state.accessToken ? { Authorization: `Bearer ${state.accessToken}` } : {}; }
async function bridgeSession() { if (!state.accessToken) return false; return (await fetch('/api/browser-session', { method: 'POST', headers: authHeaders() })).ok; }
function escapeHtml(value) { const node = document.createElement('span'); node.textContent = String(value || ''); return node.innerHTML; }
function mmk(value) { return `${Number(value || 0).toLocaleString()} ကျပ်`; }

function settings() {
  return {
    subtitles: $('#subtitleEnabled') ? $('#subtitleEnabled').checked : true,
    subtitle_text: $('#subtitleText') ? $('#subtitleText').value : 'စာတန်းနေရာ (Center)',
    subtitle_font: $('#subtitleFont') ? $('#subtitleFont').value : 'Noto Sans Myanmar',
    subtitle_size: +($('#subtitleSize') ? $('#subtitleSize').value : 20),
    subtitle_color: $('#subtitleColor') ? $('#subtitleColor').value : '#ffd166',
    subtitle_outline: $('#subtitleOutline') ? $('#subtitleOutline').value : '#000000',
    subtitle_background: $('#subtitleBackground') ? $('#subtitleBackground').value : 'solid',
    subtitle_opacity: +($('#subtitleOpacity') ? $('#subtitleOpacity').value : 70),
    subtitle_x: +($('#subtitleX') ? $('#subtitleX').value : 50),
    subtitle_y: +($('#subtitleY') ? $('#subtitleY').value : 82),
    blur_enabled: $('#blurEnabled') ? $('#blurEnabled').checked : false,
    blur_strength: +($('#blurStrength') ? $('#blurStrength').value : 18),
    blur_layer: $('#blurLayer') ? $('#blurLayer').value : 'S',
    blur_masks: state.masks,
    mirror: $('#mirror') ? $('#mirror').checked : false,
    zoom_enabled: $('#zoomEnabled') ? $('#zoomEnabled').checked : false,
    video_filter: $('#videoFilter') ? $('#videoFilter').value : 'None',
    video_bg_blur: $('#videoBgBlur') ? $('#videoBgBlur').checked : false,
    trim_start: +($('#trimStart') ? $('#trimStart').value : 0),
    trim_cover: $('#trimCover') ? $('#trimCover').checked : false,
    enable_sfx: $('#enableSfx') ? $('#enableSfx').checked : true,
    original_audio: 'Mute',
    logo_type: $('#logoType') ? $('#logoType').value : 'static',
    logo_font: $('#logoFont') ? $('#logoFont').value : 'Beloved Bunny',
    logo_path: state.uploaded.logo,
    logo_text: $('#logoText') ? $('#logoText').value : '',
    logo_size: +($('#logoSize') ? $('#logoSize').value : 22),
    logo_x: +($('#logoX') ? $('#logoX').value : 84),
    logo_y: +($('#logoY') ? $('#logoY').value : 86),
    music_path: state.uploaded.music,
    music_volume: +($('#musicVolume') ? $('#musicVolume').value : 24),
    sfx_volume: +($('#sfxVolume') ? $('#sfxVolume').value : 85),
    voice: $('#voice') ? $('#voice').value : 'Nilar',
    voice: $('#voice') ? $('#voice').value : 'Nilar',
    cloned_audio_path: state.uploaded.voice_clone,
    language: 'Myanmar',
    tone: 'Cinematic',
    voiceSpeed: $('#voiceSpeed') ? $('#voiceSpeed').value : '1.0',
    aspect: $('#aspect') ? $('#aspect').value : '9:16',
    quality: '720p',
    recapType: $('#recapType') ? $('#recapType').value : 'Movie Recap',
    groq_api_key: localStorage.getItem('one-team-groq-key') || '',
    source_url: state.isLink ? state.videoUrl : null,
    duration: state.dimensions ? state.dimensions.duration : 0,
  };
}

function renderVoiceGrid() {
  const container = $('#voiceGrid'); if (!container) return;
  const currentVoice = $('#voice') ? $('#voice').value : 'Nilar';
  container.innerHTML = VOICE_CATALOG.map((v) => `
    <div class="voice-card ${currentVoice === v.id ? 'selected' : ''}" data-voice-id="${v.id}" data-vip="${v.vip}">
      <div class="voice-card-info">
        <span class="voice-name">${escapeHtml(v.name)}</span>
      </div>
      <button class="voice-play-btn" type="button" data-play="${v.id}">▶</button>
    </div>
  `).join('');
  container.querySelectorAll('.voice-card').forEach((card) => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.voice-play-btn')) return;
      if ($('#voice')) $('#voice').value = card.dataset.voiceId;
      container.querySelectorAll('.voice-card').forEach((c) => c.classList.remove('selected'));
      card.classList.add('selected');
      if ($('#rvcModelContainer')) {
          $('#rvcModelContainer').classList.toggle('hidden', card.dataset.voiceId !== 'Voice_Clone');
      };
    });
  });
  container.querySelectorAll('.voice-play-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      playVoiceSample(btn.dataset.play, btn);
    });
  });
}

function playVoiceSample(voiceId, btn) {
  if (previewAudio) { previewAudio.pause(); previewAudio = null; $$('.voice-play-btn').forEach((b) => b.textContent = '▶'); }
  btn.textContent = '■';
  fetch('/api/preview-ms-voice', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice: voiceId }) })
  .then(res => { if (!res.ok) throw new Error("Audio fail"); return res.blob(); })
  .then(blob => { previewAudio = new Audio(URL.createObjectURL(blob)); previewAudio.play(); previewAudio.onended = () => { btn.textContent = '▶'; previewAudio = null; }; })
  .catch(() => { const utter = new SpeechSynthesisUtterance("မင်္ဂလာပါ၊ One Team မှ ကြိုဆိုပါတယ်။"); utter.rate = 1.0; window.speechSynthesis.speak(utter); btn.textContent = '▶'; });
}

// --- Key Management Functions ---
function getMaskedKey(key) {
    if (!key) return '';
    if (key.length < 15) return '****************';
    return `${key.substring(0, 4)}${'•'.repeat(16)}${key.substring(key.length - 4)}`;
}

window.renderSavedKeys = () => {
    const container = $('#savedKeysContainer');
    if (!container) return;
    const gKey = localStorage.getItem('one-team-gemini-key');
    const groqKey = localStorage.getItem('one-team-groq-key');
    let html = '';
    if (gKey) {
        html += `<div style="display: flex; justify-content: space-between; align-items: center; background: rgba(43, 136, 255, 0.15); padding: 8px 12px; border-radius: 8px; border: 1px solid rgba(43, 136, 255, 0.4); margin-bottom: ${groqKey ? '6px' : '0'};">
            <span style="font-size: 12px; color: #8bc6ff;">🤖 <b>Gemini:</b> <span style="color: #fff; font-family: monospace;">${getMaskedKey(gKey)}</span></span>
            <button type="button" onclick="removeSavedKey('gemini')" style="background: none; border: none; color: #ff5c6d; cursor: pointer; font-size: 14px; padding: 0 4px;">✖</button>
        </div>`;
    }
    if (groqKey) {
        html += `<div style="display: flex; justify-content: space-between; align-items: center; background: rgba(255, 107, 107, 0.15); padding: 8px 12px; border-radius: 8px; border: 1px solid rgba(255, 107, 107, 0.4);">
            <span style="font-size: 12px; color: #ff6b6b;">⚡ <b>Groq:</b> <span style="color: #fff; font-family: monospace;">${getMaskedKey(groqKey)}</span></span>
            <button type="button" onclick="removeSavedKey('groq')" style="background: none; border: none; color: #ff5c6d; cursor: pointer; font-size: 14px; padding: 0 4px;">✖</button>
        </div>`;
    }
    container.innerHTML = html;
};

window.removeSavedKey = (type) => { 
    if(type === 'gemini') localStorage.removeItem('one-team-gemini-key'); 
    if(type === 'groq') localStorage.removeItem('one-team-groq-key'); 
    renderSavedKeys(); 
};

window.oneTeamSaveKey = () => {
    const keyInput = $('#apiKey');
    const groqInput = $('#groqApiKey');
    if (keyInput && keyInput.value) { localStorage.setItem('one-team-gemini-key', keyInput.value.trim()); keyInput.value = ''; }
    if (groqInput && groqInput.value) { localStorage.setItem('one-team-groq-key', groqInput.value.trim()); groqInput.value = ''; }
    renderSavedKeys();
    if ($('#keySheet')) $('#keySheet').classList.add('hidden');
    return true;
};

function savedKey() { return localStorage.getItem('one-team-gemini-key') || ''; }
function requiresUserKey() { return true; }

async function checkApiKey() {
    const statusMsg = $('#keyStatusMsg');
    let keyToCheck = $('#apiKey') ? String($('#apiKey').value || '').trim() : '';
    if (!keyToCheck) keyToCheck = localStorage.getItem('one-team-gemini-key');

    if (!keyToCheck) {
        statusMsg.style.display = 'block'; statusMsg.style.color = '#ffb5bf'; statusMsg.textContent = '❌ စစ်ဆေးရန် API Key အရင်ထည့်ပါ။';
        return;
    }

    statusMsg.style.display = 'block'; statusMsg.style.color = '#ffd166'; statusMsg.textContent = '⏳ ဆာဗာများနှင့် ချိတ်ဆက်စစ်ဆေးနေပါသည်...';
    try {
        let res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models?key=${keyToCheck}`);
        statusMsg.style.color = '#fff';
        statusMsg.innerHTML = res.ok ? '✅ <b>Gemini:</b> အလုပ်လုပ်ပါသည်။' : '❌ <b>Gemini:</b> Limit ပြည့်နေ/မှားနေပါသည်။';
    } catch (e) {
        statusMsg.style.color = '#fff'; statusMsg.innerHTML = '❌ <b>Gemini:</b> ချိတ်ဆက်၍ မရပါ။';
    }
}

function redraw() {
  if (!layer) return;
  layer.replaceChildren(); const set = settings();
  if (set.blur_enabled && (state.activeTab === 'blur' || state.activeTab === 'subtitle')) { state.masks.forEach((mask, index) => addBoxElement(mask, index, state.activeTab === 'subtitle')); }
  if ((state.activeTab === 'subtitle' || state.activeTab === 'blur') && set.subtitles) { addSubtitleElement(set); }
  
  if (state.activeTab === 'logo') {
      if (set.logo_type === 'static') {
          addLogoElement(set);
      } else if (set.logo_type === 'moving') {
                const area = displayRect();
                const node = document.createElement('marquee');
                node.className = 'test-logo-overlay';
                node.textContent = set.logo_text || 'Test Logo';
                node.style.fontFamily = `"${set.logo_font}", cursive`;
                node.style.position = 'absolute';
                node.style.top = `${set.logo_y}%`;
                node.style.width = `${area.width}px`;
                node.style.color = 'white';
                node.style.fontSize = `${Math.max(16, area.width * (set.logo_size / 100))}px`;
                node.style.fontWeight = 'normal';
                node.style.textShadow = '2px 2px 4px #000';
                node.style.pointerEvents = 'none';
                layer.append(node);
            }
  }
}

function addSubtitleElement(set) {
  const node = document.createElement('div'); 
  node.className = `subtitle-overlay ${String(set.subtitle_background).toLowerCase() === 'solid' || String(set.subtitle_background) === 'Solid background' ? 'solid' : ''}`;
  node.textContent = set.subtitle_text || 'စာတန်းနေရာ (Center)'; 
  node.style.fontFamily = set.subtitle_font; 
  node.style.color = set.subtitle_color; 
  node.style.textShadow = `2px 2px 2px ${set.subtitle_outline},-2px -2px 2px ${set.subtitle_outline}`;
  node.style.fontSize = `${Math.max(16, set.subtitle_size * 1.2)}px`; 
  node.style.border = '2px dashed rgba(255, 255, 255, 0.7)'; 
  node.style.textAlign = 'center'; node.style.whiteSpace = 'nowrap'; node.style.padding = '4px 8px';
  layer.append(node);
  const area = displayRect(); 
  const p = stagePoint(set.subtitle_x, set.subtitle_y);
  node.style.left = `${clamp(p.x - node.offsetWidth / 2, area.left, area.left + area.width - node.offsetWidth)}px`; 
  node.style.top = `${clamp(p.y - node.offsetHeight / 2, area.top, area.top + area.height - node.offsetHeight)}px`; 
  dragNode(node, 'subtitle');
}

function addBoxElement(mask, index, isGhost = false) {
  const area = displayRect(); const node = document.createElement('div'); 
  node.className = `blur-box ${isGhost ? 'ghost-blur' : ''}`; node.dataset.index = index;
  node.style.left = `${area.left + mask.x * area.width}px`; node.style.top = `${area.top + mask.y * area.height}px`; 
  node.style.width = `${mask.width * area.width}px`; node.style.height = `${mask.height * area.height}px`;
  if (!isGhost) { const handle = document.createElement('i'); handle.className = 'resize-handle'; node.append(handle); dragNode(node, 'blur'); }
  layer.append(node);
}

function addLogoElement(set) {
  const area = displayRect(); const node = document.createElement('div'); node.className = 'logo-overlay'; 
  node.style.width = `${clamp(area.width * set.logo_size / 100, 72, area.width)}px`;
  if (state.logoUrl) { const image = document.createElement('img'); image.src = state.logoUrl; node.append(image); }
  if (set.logo_text) { 
              const text = document.createElement('span'); 
              text.textContent = set.logo_text; 
              text.style.fontFamily = `"${set.logo_font}", cursive`;
              text.style.fontWeight = 'normal';
              node.append(text); 
          }
  if (!node.childNodes.length) node.textContent = 'Logo'; const handle = document.createElement('i'); handle.className = 'resize-handle'; node.append(handle); layer.append(node);
  const p = stagePoint(set.logo_x, set.logo_y); node.style.left = `${clamp(p.x - node.offsetWidth / 2, area.left, area.left + area.width - node.offsetWidth)}px`; node.style.top = `${clamp(p.y - node.offsetHeight / 2, area.top, area.top + area.height - node.offsetHeight)}px`; dragNode(node, 'logo');
}

function dragNode(node, type) {
  node.addEventListener('pointerdown', (event) => { event.preventDefault(); node.setPointerCapture(event.pointerId); const r = node.getBoundingClientRect(); const s = stage.getBoundingClientRect(); state.dragging = { node, type, resize: event.target.classList.contains('resize-handle'), sx: event.clientX, sy: event.clientY, x: r.left - s.left, y: r.top - s.top, w: r.width, h: r.height }; });
  node.addEventListener('pointermove', (event) => { 
    const d = state.dragging; if (!d || d.node !== node) return; 
    const area = displayRect(); const dx = event.clientX - d.sx; const dy = event.clientY - d.sy; 
    if (d.resize && type === 'blur') { d.w = clamp(d.w + dx, 24, area.left + area.width - d.x); d.h = clamp(d.h + dy, 24, area.top + area.height - d.y); node.style.width = `${d.w}px`; node.style.height = `${d.h}px`; } 
    else if (d.resize && type === 'logo') { d.w = clamp(d.w + dx, 72, area.left + area.width - d.x); node.style.width = `${d.w}px`; } 
    else { d.x = clamp(d.x + dx, area.left, area.left + area.width - node.offsetWidth); d.y = clamp(d.y + dy, area.top, area.top + area.height - node.offsetHeight); node.style.left = `${d.x}px`; node.style.top = `${d.y}px`; } 
    d.sx = event.clientX; d.sy = event.clientY; 
  });
  const finish = () => { 
    const d = state.dragging; if (!d || d.node !== node) return; 
    if (type === 'subtitle') { const p = relativePoint(d.x + node.offsetWidth / 2, d.y + node.offsetHeight / 2); if($('#subtitleX')) $('#subtitleX').value = Math.round(p.x * 100); if($('#subtitleY')) $('#subtitleY').value = Math.round(p.y * 100); output($('#subtitleX'), '#subtitleXValue'); output($('#subtitleY'), '#subtitleYValue'); } 
    else if (type === 'logo') { const p = relativePoint(d.x + node.offsetWidth / 2, d.y + node.offsetHeight / 2); const area = displayRect(); if($('#logoX')) $('#logoX').value = Math.round(p.x * 100); if($('#logoY')) $('#logoY').value = Math.round(p.y * 100); if($('#logoSize')) $('#logoSize').value = Math.round(clamp(node.offsetWidth / area.width * 100, 10, 45)); output($('#logoX'), '#logoXValue'); output($('#logoY'), '#logoYValue'); output($('#logoSize'), '#logoSizeValue'); } 
    else { const p = relativePoint(d.x, d.y); const area = displayRect(); const index = +node.dataset.index; state.masks[index] = { x: p.x, y: p.y, width: clamp(node.offsetWidth / area.width, .01, 1 - p.x), height: clamp(node.offsetHeight / area.height, .01, 1 - p.y) }; } 
    state.dragging = null; 
  };
  node.addEventListener('pointerup', finish); node.addEventListener('pointercancel', finish); node.addEventListener('lostpointercapture', finish);
}

function selectTab(tab) { state.activeTab = tab; $$('.tab').forEach((item) => item.classList.toggle('active', item.dataset.tab === tab)); $$('.tab-page').forEach((page) => page.classList.toggle('active', page.dataset.page === tab)); redraw(); }
async function uploadAsset(input, kind) { const file = input.files?.[0]; if (!file) return; const data = new FormData(); data.append('file', file); data.append('kind', kind); data.append('project_id', state.projectId); const response = await fetch('/api/assets', { method: 'POST', body: data }); const body = await response.json(); if (!response.ok) { alert(body.detail || 'ဖိုင်တင်မရပါ'); return; } state.uploaded[kind] = body.path; input.closest('label').classList.add('uploaded'); if (kind === 'logo') { if (state.logoUrl) URL.revokeObjectURL(state.logoUrl); state.logoUrl = URL.createObjectURL(file); redraw(); } }

// ----------------- Local VPS Direct Upload Implementation -----------------
async function uploadVideo(file) { 
    if($('#videoName')) $('#videoName').textContent = "VPS သို့ Upload လုပ်နေပါသည်...";
    if($('#videoMeta')) $('#videoMeta').textContent = "ခဏစောင့်ပါ...";
  
    try {
        const formData = new FormData();
        formData.append("project_id", state.projectId);
        formData.append("file", file);

        const res = await fetch('/api/upload-video-local', {
            method: 'POST',
            body: formData
        });
        
        if (!res.ok) throw new Error("Upload မအောင်မြင်ပါ");
        const info = await res.json();
        
        state.dimensions = info; 
        if($('#videoName')) $('#videoName').textContent = file.name; 
        if($('#videoMeta')) $('#videoMeta').textContent = `${Math.round(info.duration)} sec · Ready`; 
        if($('#sessionStatus')) $('#sessionStatus').textContent = 'Ready to edit'; 
        if($('#exportButton')) $('#exportButton').disabled = false;
        
    } catch (error) {
        alert(error.message);
        if($('#videoName')) $('#videoName').textContent = "Video ဖိုင်တင်ပါ (သို့) Link ထည့်ပါ";
        if($('#videoMeta')) $('#videoMeta').textContent = "ဖိုင်တင်ပြီးရင် video ပေါ်မှာတိုက်ရိုက်ပြင်နိုင်မယ်";
        if($('#emptyState')) $('#emptyState').classList.remove('hidden'); 
        if(stage) stage.classList.add('hidden'); 
    }
}
// ----------------------------------------------------------------------

function selectFile(event) { 
  const file = event.target.files?.[0]; if (!file) return; state.video = file; 
  if (state.videoUrl) URL.revokeObjectURL(state.videoUrl); state.videoUrl = URL.createObjectURL(file); 
  if (video) video.src = state.videoUrl; 
  if($('#emptyState')) $('#emptyState').classList.add('hidden'); 
  if(stage) stage.classList.remove('hidden'); 
  uploadVideo(file).catch((error) => alert(error.message)); 
}

function setJob(job) { 
    const progress = +job.progress || 0; 
    if($('#jobProgress')) $('#jobProgress').style.width = `${progress}%`; 
    if($('#jobPercent')) $('#jobPercent').textContent = `${progress}%`; 
    if($('#jobTitle')) $('#jobTitle').textContent = job.message || 'Final Video လုပ်နေပါတယ်'; 
    const order = ['upload', 'script', 'voice', 'render']; const active = order.indexOf(job.step); 
    $$('#jobSteps li').forEach((li, index) => li.className = index < active ? 'done' : index === active ? 'active' : ''); 
    if (job.status === 'done') { 
        if($('#downloadContainer')) { $('#downloadContainer').classList.remove('hidden'); $('#downloadContainer').style.display = 'flex'; }
        if($('#downloadButton')) { $('#downloadButton').href = job.download_url; $('#downloadButton').classList.remove('hidden'); }
        if($('#downloadThumbBtn')) { if (job.thumb_url) { $('#downloadThumbBtn').href = job.thumb_url; $('#downloadThumbBtn').style.display = 'block'; } else { $('#downloadThumbBtn').style.display = 'none'; } }
        if($('#cancelJob')) $('#cancelJob').classList.add('hidden'); 
        if($('#jobTitle')) $('#jobTitle').textContent = 'Final MP4 အဆင်သင့်ပါပြီ'; 
        
        /* Final Video ကို မျက်နှာပြင်မှာ တန်းပေါ်စေရန် ထပ်ဖြည့်ထားသည့် Code */
        if (video && job.download_url) {
            video.src = job.download_url;
            video.controls = true;
            video.load();
            video.play().catch(e => console.log("Auto-play prevented"));
            if ($('#emptyState')) $('#emptyState').classList.add('hidden');
            if (stage) stage.classList.remove('hidden');
        }
    } 
    if (['error', 'cancelled'].includes(job.status)) { 
        if($('#jobError')) { 
            let errorText = job.error || 'Video ထုတ်မရပါ';
            
            // Backend မှ [API_ERROR] ပါလာရင် လုပ်ဆောင်မည့် အပိုင်း
            if (errorText.includes("[API_ERROR]")) {
                errorText = errorText.replace("[API_ERROR]", "⚠️️").trim();
                
                // ၁။ စက်ထဲတွင် မှတ်ထားသော Key အဟောင်းကို ချက်ချင်း ဖျက်ပစ်မည်
                localStorage.removeItem('one-team-gemini-key');
                if($('#apiKey')) $('#apiKey').value = '';
                
                // ၂။ User ဖတ်လို့ရအောင် ၂.၅ စက္ကန့်လောက် စောင့်ပြီးမှ Key ထည့်ရန် Box ကို Auto ပြန်ပေါ်စေမည်
                setTimeout(() => {
                    if($('#keySheet')) {
                        $('#keySheetHint').textContent = "သင့်အရင် Key သည် အလုပ်မလုပ်တော့ပါ သို့မဟုတ် Limit ပြည့်သွားပါသည်။ အသစ်ထည့်ပါ။";
                        $('#keySheetHint').style.color = "#ffbdc5";
                        $('#keySheet').classList.remove('hidden');
                    }
                }, 2500); 
            }
            
            $('#jobError').textContent = errorText; 
            $('#jobError').classList.remove('hidden'); 
        }
        if($('#cancelJob')) $('#cancelJob').classList.add('hidden'); 
    } 
}

async function exportVideo() { 
    if (!state.video && !state.videoUrl) return; 
    if (state.auth) { const { data: { session } } = await state.auth.auth.getSession(); state.accessToken = session ? session.access_token : ''; }
    if (!state.accessToken) { window.location.assign('/login'); return; } 
    if (requiresUserKey() && !savedKey()) { if($('#keySheet')) $('#keySheet').classList.remove('hidden'); return; } 
    if($('#jobCard')) $('#jobCard').classList.remove('hidden'); 
    if($('#jobError')) $('#jobError').classList.add('hidden'); 
    if($('#downloadContainer')) { $('#downloadContainer').classList.add('hidden'); $('#downloadContainer').style.display = 'none'; }
    if($('#downloadButton')) $('#downloadButton').classList.add('hidden'); 
    if($('#downloadThumbBtn')) $('#downloadThumbBtn').style.display = 'none';
    if($('#cancelJob')) $('#cancelJob').classList.remove('hidden'); 
    if($('#exportButton')) $('#exportButton').disabled = true; 
    try { 
        const response = await fetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ project_id: state.projectId, settings: settings(), simple_api_key: savedKey() }) }); 
        const job = await response.json(); 
        if (!response.ok) throw new Error(job.detail || 'Export မစနိုင်ပါ'); 
        state.jobId = job.id; setJob(job); pollJob(); 
    } catch (error) { 
        if($('#jobError')) { $('#jobError').textContent = error.message; $('#jobError').classList.remove('hidden'); }
        if($('#exportButton')) $('#exportButton').disabled = false; 
    } 
}

async function pollJob() { if (!state.jobId) return; const response = await fetch(`/api/jobs/${state.jobId}`); const job = await response.json(); setJob(job); if (['queued', 'running'].includes(job.status)) window.setTimeout(pollJob, 1500); else if($('#exportButton')) $('#exportButton').disabled = false; }

function planTitle(member) { 
  if (!member) return 'Account ဝင်မယ်'; if (member.is_admin) return 'Owner Pro'; 
  const credits = member.credit_balance || 0; const days = member.trial_days_remaining || 0; let creditInfo = credits > 0 ? ` · ${credits} Credits` : '';
  if (member.plan === 'simple') return `Free Account${creditInfo}`; 
  if (member.plan === 'pro') return `${member.subscription_tier === 'gold_vip' ? 'Gold VIP' : 'Silver VIP'} (${days} ရက်)${creditInfo}`; 
  return credits > 0 ? `${credits} Credits` : (member.trial_expired ? 'Trial ကုန်ပြီ' : 'Plan ဝယ်မယ်'); 
}

function setMembership(member) { 
  state.member = member || null; renderVoiceGrid();
  const title = planTitle(member); 
  if($('#sideAdminBtn')) $('#sideAdminBtn').classList.toggle('hidden', !member || !member.is_admin); 
  if($('#sessionStatus')) $('#sessionStatus').textContent = member ? `${title} · ${member.email}` : 'Account ဝင်ပြီးမှ Export လုပ်ပါ'; 
  const daysSpan = $('#daysRemaining');
  if (daysSpan) { if (member && member.plan === 'pro') { const days = member.trial_days_remaining || 0; daysSpan.textContent = `VIP: ${days} ရက်`; } else { daysSpan.textContent = ''; } }

  const tgBtn = $('#tgConnectBtn');
  if (tgBtn) {
      if (member && member.email) {
          /* Telegram Bot နာမည်ကို သင့် Bot အမှန်ပြောင်းပေးပါ */
          const botUsername = "Onelay_Bot"; 
          // Telegram လက်ခံစေရန် @ နှင့် . ကို ဖျောက်ပြီး ပို့ပါမည်
          const safeEmail = member.email.replace(/@/g, '_at_').replace(/\./g, '_dot_');
          tgBtn.href = `https://t.me/${botUsername}?start=${safeEmail}`;
          tgBtn.style.display = 'inline-block';
      } else { tgBtn.style.display = 'none'; }
  }
}

async function refreshMembership() { 
  if (!state.accessToken) return;
  try {
      const response = await fetch('/api/membership', { headers: authHeaders() }); 
      if (!response.ok) { const errorData = await response.json().catch(() => ({})); if($('#sessionStatus')) $('#sessionStatus').textContent = errorData.detail || 'Account အချက်အလက် ယူမရပါ'; return; }
      setMembership(await response.json()); 
  } catch (e) { if($('#sessionStatus')) $('#sessionStatus').textContent = 'Connection error'; }
}

async function loadPlans() { if (state.catalog) return state.catalog; const response = await fetch('/api/plans'); state.catalog = await response.json(); return state.catalog; }
function updateDestination() { const item = state.catalog && state.catalog.destinations && $('#paymentMethod') ? state.catalog.destinations[$('#paymentMethod').value] : null; if($('#paymentDestination')) $('#paymentDestination').textContent = item ? `${$('#paymentMethod').value} · ${item.phone} · ${item.account_name}` : ''; }

function openPayment(kind, key) { 
  const items = kind === 'plan' ? (state.catalog ? state.catalog.plans : []) : (state.catalog ? state.catalog.credit_packs : []); const offer = items ? items.find((item) => item.key === key) : null; 
  if (!offer) return; state.selectedPayment = { kind, key, offer }; 
  if($('#paymentSelected')) $('#paymentSelected').textContent = `${offer.label} · ${mmk(offer.price)} · ${offer.description}`; 
  if($('#paymentForm')) { $('#paymentForm').classList.remove('hidden'); $('#paymentForm').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
  if($('#paymentNote')) $('#paymentNote').textContent = ''; updateDestination(); 
}

function renderOffers(catalog) { 
  const plans = (catalog.plans || []).map((item) => `<article class="offer"><div><strong>${escapeHtml(item.label)}</strong><p>${escapeHtml(item.description)}</p><b>${mmk(item.price)} / 30 ရက်</b></div><button data-kind="plan" data-key="${escapeHtml(item.key)}" type="button">ဝယ်ယူမည်</button></article>`).join(''); 
  const credits = (catalog.credit_packs || []).map((item) => `<article class="offer"><div><strong>${escapeHtml(item.label)}</strong><p>${escapeHtml(item.description)}</p><b>${mmk(item.price)}</b></div><button data-kind="credits" data-key="${escapeHtml(item.key)}" type="button">ဝယ်ယူမည်</button></article>`).join('');
  if($('#planList')) $('#planList').innerHTML = `<p class="plan-section-title">VIP Monthly Plans (လစဉ်ကြေး)</p>${plans}<p class="plan-section-title" style="margin-top: 15px;">Credit Packs (ခရက်ဒစ် အထုပ်များ)</p>${credits}`; 
}

async function openPlanSheet() { if (!state.accessToken) { window.location.assign('/login'); return; } if($('#planSheet')) $('#planSheet').classList.remove('hidden'); try { const catalog = await loadPlans(); renderOffers(catalog); const pending = state.member && state.member.pending_payment ? ' · Payment စစ်နေပါတယ်' : ''; if($('#accountStatus')) $('#accountStatus').textContent = `${planTitle(state.member)}${pending} · Video ကိုစိတ်ကြိုက်ထုတ်လို့ရပါတယ်`; updateDestination(); } catch (e) { if($('#accountStatus')) $('#accountStatus').textContent = 'Plan အချက်အလက်မရသေးပါ'; } }

async function submitPayment() { if (!state.selectedPayment) return; const transactionId = $('#transactionId') ? $('#transactionId').value.trim() : ''; if (!transactionId || transactionId.length < 4) { if($('#paymentNote')) $('#paymentNote').textContent = 'Transaction ID မှန်မှန်ထည့်ပါ'; return; } const form = new FormData(); form.append('kind', state.selectedPayment.kind); form.append('offer_key', state.selectedPayment.key); form.append('payment_method', $('#paymentMethod') ? $('#paymentMethod').value : ''); form.append('transaction_id', transactionId); const receipt = $('#receiptInput') && $('#receiptInput').files ? $('#receiptInput').files[0] : null; if (receipt) form.append('receipt', receipt); if($('#submitPayment')) $('#submitPayment').disabled = true; if($('#paymentNote')) $('#paymentNote').textContent = 'ပို့နေပါတယ်…'; try { const response = await fetch('/api/payments', { method: 'POST', headers: authHeaders(), body: form }); const body = await response.json(); if (!response.ok) throw new Error(body.detail || 'ငွေလွှဲအချက်အလက်မပို့နိုင်ပါ'); if($('#paymentNote')) $('#paymentNote').textContent = body.message; if($('#paymentForm')) $('#paymentForm').classList.add('hidden'); await refreshMembership(); } catch (error) { if($('#paymentNote')) $('#paymentNote').textContent = error.message; } finally { if($('#submitPayment')) $('#submitPayment').disabled = false; } }

async function initializeAuth() { 
  try { 
    const configRes = await fetch('/api/auth-config'); const config = await configRes.json(); 
    if (config.enabled !== 'true' || !window.supabase) { if($('#sessionStatus')) $('#sessionStatus').textContent = 'Account Database setting မပြည့်သေးပါ'; return; } 
    state.auth = window.supabase.createClient(config.url, config.anon_key, { auth: { persistSession: true, autoRefreshToken: true } }); 
    const { data: { session } } = await state.auth.auth.getSession(); state.accessToken = session ? session.access_token : ''; 
    if (!state.accessToken || !await bridgeSession()) { if (window.location.pathname !== '/login') window.location.replace('/login'); return; } 
    await refreshMembership(); 
    state.auth.auth.onAuthStateChange(async (_event, sessionNow) => { state.accessToken = sessionNow ? sessionNow.access_token : ''; if (!state.accessToken) { await fetch('/api/browser-session', { method: 'DELETE' }); if (window.location.pathname !== '/login') window.location.replace('/login'); } else if (await bridgeSession()) { refreshMembership(); } }); 
  } catch (err) { if($('#sessionStatus')) $('#sessionStatus').textContent = 'Account စစ်မရသေးပါ။ ခဏနောက်ပြန်စမ်းပါ'; } 
}

const toggleSidebar = (show) => { if($('#sidebarOverlay')) $('#sidebarOverlay').classList.toggle('hidden', !show); if($('#sidebarMenu')) $('#sidebarMenu').classList.toggle('hidden', !show); };

let currentHistoryData = [];

async function loadHistory() {
    const list = $('#historyList'); if (!list) return;
    list.innerHTML = '<p style="text-align: center; color: #94a3b8; font-size: 14px; margin-top: 40px; grid-column: 1/-1;">မှတ်တမ်း ရှာနေပါသည်...</p>';
    try {
        const res = await fetch('/api/history', { headers: authHeaders() }); 
        if (!res.ok) throw new Error(); 
        currentHistoryData = await res.json();
        renderHistoryCards('all');
    } catch (e) { 
        list.innerHTML = '<p style="text-align: center; color: #ef4444; font-size: 14px; margin-top: 40px; grid-column: 1/-1;">မှတ်တမ်း ရယူ၍ မရပါ</p>'; 
    }
}

function renderHistoryCards(filterType) {
    const list = $('#historyList'); if (!list) return;

    let rCount = 0, sCount = 0, fCount = 0;
    currentHistoryData.forEach(item => {
        if (item.status === 'success' || item.status === 'done') sCount++;
        else if (item.status === 'fail' || item.status === 'error' || item.status === 'cancelled') fCount++;
        else rCount++;
    });

    if ($('#countRunning')) $('#countRunning').textContent = rCount;
    if ($('#countSuccess')) $('#countSuccess').textContent = sCount;
    if ($('#countFail')) $('#countFail').textContent = fCount;

    const filteredData = currentHistoryData.filter(item => {
        if (filterType === 'all') return true;
        const isSuccess = (item.status === 'success' || item.status === 'done');
        const isFail = (item.status === 'fail' || item.status === 'error' || item.status === 'cancelled');
        if (filterType === 'success') return isSuccess;
        if (filterType === 'fail') return isFail;
        if (filterType === 'running') return !isSuccess && !isFail;
        return true;
    });

    if (!filteredData || filteredData.length === 0) { 
        list.innerHTML = '<p style="text-align: center; color: #94a3b8; font-size: 14px; margin-top: 40px; grid-column: 1/-1;">ရွေးချယ်ထားသော မှတ်တမ်း မရှိပါ</p>'; 
        return; 
    }

    list.innerHTML = filteredData.map(item => {
        const isSuccess = (item.status === 'success' || item.status === 'done'); 
        let statusBadge = '';
        let errorBtn = '';

        if (isSuccess) {
            statusBadge = '<span class="history-status-badge success" style="position: static; display: inline-block; margin-bottom: 8px;">✅ ပြီးဆုံး</span>';
        } else if (item.status === 'running' || item.status === 'queued') {
            statusBadge = `<span class="history-status-badge" style="position: static; display: inline-block; margin-bottom: 8px; background: rgba(59, 130, 246, 0.2); color: #3b82f6; border: 1px solid rgba(59, 130, 246, 0.4);">⏳ လုပ်နေဆဲ (${item.progress || 0}%)</span>`;
        } else {
            statusBadge = '<span class="history-status-badge fail" style="position: static; display: inline-block; margin-bottom: 8px;">❌ မအောင်မြင်ပါ</span>';
            errorBtn = `<button onclick="alert('⚠️ အကြောင်းရင်း: ' + '${escapeHtml(item.message || 'အမည်မသိ Error').replace(/'/g, '\\\'')}')" style="background: #3f1616; color: #f87171; border: 1px solid #7f1d1d; padding: 4px 8px; border-radius: 6px; font-size: 10px; cursor: pointer; margin-left: 8px; font-weight: bold; margin-bottom: 8px;">⚠️ အကြောင်းရင်းကြည့်မယ်</button>`;
        }
        
        const dur = Math.round(item.duration); const mins = Math.floor(dur / 60); const secs = dur % 60; const durText = `${mins}:${secs.toString().padStart(2, '0')}`;
        
        return `<div class="history-card">
                  <div class="history-details">
                    <div style="display: flex; justify-content: space-between; align-items: start;">
                       <div>
                         ${statusBadge}
                         ${errorBtn}
                       </div>
                       <span style="font-size: 11px; color: #94a3b8; font-weight: bold;">⏱ ${durText}</span>
                    </div>
                    <div class="history-title">${escapeHtml(item.filename || 'Video Recap')}</div>
                    <div class="history-date">📅 ${escapeHtml(item.date)}</div>${isSuccess ? `<div class="history-actions" style="grid-template-columns: ${item.thumb_url ? '1fr 1fr' : '1fr'}; gap: 8px;">
                      ${item.download_url ? `<a href="${item.download_url}" class="history-btn dl-video" download>🎬 Video ဒေါင်းရန်</a>` : ''}
                      ${item.thumb_url ? `<a href="${item.thumb_url}" class="history-btn dl-thumb" download>📸 Cover ဒေါင်းရန်</a>` : ''}
                    </div>` : ''}
                  </div>
                </div>`;
    }).join('');
}

function attachEvt(selector, evt, handler) { const el = $(selector); if (el) el.addEventListener(evt, handler); }

try {
    attachEvt('#menuButton', 'click', () => toggleSidebar(true)); attachEvt('#closeSidebar', 'click', () => toggleSidebar(false)); attachEvt('#sidebarOverlay', 'click', () => toggleSidebar(false));
    const openKeyPopup = () => {
        toggleSidebar(false);
        if ($('#keySheet')) {
            if ($('#apiKey')) $('#apiKey').value = '';
            if ($('#keyStatusMsg')) $('#keyStatusMsg').style.display = 'none';
            if (typeof renderSavedKeys === 'function') renderSavedKeys();
            $('#keySheet').classList.remove('hidden');
        }
    };

    attachEvt('#topKeyBtn', 'click', openKeyPopup);   // 🔑 Topbar သရဖူဘေး ခလုတ်
    attachEvt('#sideKeyBtn', 'click', openKeyPopup);  // 🔑 Sidebar ထဲက ခလုတ်
    attachEvt('#topPlanBtn', 'click', openPlanSheet);
    attachEvt('#sideHistoryBtn', 'click', () => { toggleSidebar(false); if($('#historySheet')) $('#historySheet').classList.remove('hidden'); loadHistory(); });
    attachEvt('#sideAdminBtn', 'click', () => window.location.assign('/owner'));
    attachEvt('#checkKeyBtn', 'click', checkApiKey);
    attachEvt('#apiKey', 'focus', () => {
        if ($('#keyStatusMsg')) $('#keyStatusMsg').style.display = 'none';
    });
    attachEvt('#closeHistorySheet', 'click', () => { if($('#historySheet')) $('#historySheet').classList.add('hidden'); }); attachEvt('#closeKeySheet', 'click', () => { if($('#keySheet')) $('#keySheet').classList.add('hidden'); }); attachEvt('#closePlanSheet', 'click', () => { if($('#planSheet')) $('#planSheet').classList.add('hidden'); }); 
    $$('.tab').forEach((button) => button.addEventListener('click', () => selectTab(button.dataset.tab)));
    attachEvt('#videoInput', 'change', selectFile); attachEvt('#logoInput', 'change', (event) => uploadAsset(event.target, 'logo')); attachEvt('#musicInput', 'change', (event) => uploadAsset(event.target, 'music'));
    attachEvt('#rvcTargetAudio', 'change', (event) => uploadAsset(event.target, 'voice_clone'));
    attachEvt('#addBlur', 'click', () => { state.masks.push({ x: .32, y: .3, width: .3, height: .16 }); if($('#blurEnabled')) $('#blurEnabled').checked = true; redraw(); });
    
    ['subtitleEnabled', 'blurEnabled', 'blurLayer', 'subtitleText', 'subtitleFont', 'subtitleColor', 'subtitleOutline', 'subtitleBackground', 'mirror', 'zoomEnabled', 'videoFilter', 'videoBgBlur', 'logoType', 'logoFont', 'logoText', 'aspect', 'recapType', 'voice', 'voiceSpeed', 'trimCover', 'enableSfx'].forEach((id) => { const el = $(`#${id}`); if (el) ['input', 'change'].forEach((event) => el.addEventListener(event, redraw)); });
    
    [['subtitleSize', '#subtitleSizeValue'], ['subtitleOpacity', '#subtitleOpacityValue'], ['subtitleX', '#subtitleXValue'], ['subtitleY', '#subtitleYValue'], ['blurStrength', '#blurStrengthValue'], ['musicVolume', '#musicVolumeValue'], ['sfxVolume', '#sfxVolumeValue'], ['trimStart', '#trimStartValue'], ['logoSize', '#logoSizeValue'], ['logoX', '#logoXValue'], ['logoY', '#logoYValue']].forEach(([input, target]) => { const el = $(`#${input}`); if (el) el.addEventListener('input', () => { output(el, target, ''); redraw(); }); });
    if (video) { video.addEventListener('loadedmetadata', () => { redraw(); requestAnimationFrame(redraw); window.setTimeout(redraw, 50); }); video.addEventListener('loadeddata', redraw); }
    window.addEventListener('resize', redraw);
    attachEvt('#exportButton', 'click', exportVideo); attachEvt('#cancelJob', 'click', () => fetch(`/api/jobs/${state.jobId}/cancel`, { method: 'POST' })); attachEvt('#saveKey', 'click', window.oneTeamSaveKey); attachEvt('#paymentMethod', 'change', updateDestination); attachEvt('#planList', 'click', (event) => { const button = event.target.closest('button[data-kind]'); if (button) openPayment(button.dataset.kind, button.dataset.key); }); attachEvt('#submitPayment', 'click', submitPayment);
    const fetchBtn = $('#fetchLinkBtn');
    if (fetchBtn) {
        fetchBtn.addEventListener('click', async () => {
            const url = $('#linkInput') ? $('#linkInput').value.trim() : ''; if (!url) { alert('Link ထည့်ပေးပါ'); return; }
            if($('#linkInputContainer')) $('#linkInputContainer').classList.add('hidden'); if($('#uploadHint')) $('#uploadHint').classList.add('hidden'); if($('#uploadLabel')) $('#uploadLabel').classList.add('hidden'); if($('#linkLoadingContainer')) $('#linkLoadingContainer').classList.remove('hidden'); if($('#sessionStatus')) $('#sessionStatus').textContent = 'Video ကို ဆာဗာသို့ချိတ်ဆက်နေပါသည်...';
            try {
                const res = await fetch('/api/link-preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) }); const data = await res.json(); if (!res.ok) throw new Error(data.detail || 'Link ဖွင့်မရပါ');
                const dlJobId = data.job_id;
                const pollDl = async () => {
                    try {
                        const pRes = await fetch(`/api/jobs/${dlJobId}`); const job = await pRes.json();
                        if (job.status === 'error') throw new Error(job.error || 'ဒေါင်းလုဒ်ဆွဲ၍ မရပါ။');
                        if (job.status === 'running') { const pct = job.progress || 0; if($('#linkProgressText')) $('#linkProgressText').textContent = job.message || `Video ကို ဆွဲယူနေပါသည်... ${pct}%`; if($('#linkProgressBar')) $('#linkProgressBar').style.width = `${pct}%`; setTimeout(pollDl, 1500); } 
                        else if (job.status === 'done') { state.projectId = job.project_id; state.videoUrl = job.video_url; state.isLink = true; state.video = { name: "Link_Video.mp4" }; state.dimensions = { duration: job.duration }; if (video) { video.src = job.video_url; video.load(); } if($('#emptyState')) $('#emptyState').classList.add('hidden'); if(stage) stage.classList.remove('hidden'); if($('#videoName')) $('#videoName').textContent = "Link မှ Video"; if($('#sessionStatus')) $('#sessionStatus').textContent = 'Ready to edit'; if($('#exportButton')) $('#exportButton').disabled = false; }
                    } catch (e) { alert(e.message); if($('#sessionStatus')) $('#sessionStatus').textContent = e.message; if($('#linkLoadingContainer')) $('#linkLoadingContainer').classList.add('hidden'); if($('#linkInputContainer')) $('#linkInputContainer').classList.remove('hidden'); if($('#uploadHint')) $('#uploadHint').classList.remove('hidden'); if($('#uploadLabel')) $('#uploadLabel').classList.remove('hidden'); }
                }; pollDl();
            } catch (e) { alert(e.message); if($('#sessionStatus')) $('#sessionStatus').textContent = e.message; if($('#linkLoadingContainer')) $('#linkLoadingContainer').classList.add('hidden'); if($('#linkInputContainer')) $('#linkInputContainer').classList.remove('hidden'); if($('#uploadHint')) $('#uploadHint').classList.remove('hidden'); if($('#uploadLabel')) $('#uploadLabel').classList.remove('hidden'); }
        });
    }
    attachEvt('#refreshHistoryBtn', 'click', loadHistory);
    $$('.filter-chip, .stat-card').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const filter = e.currentTarget.dataset.filter;
            if (!filter) return;
            $$('.filter-chip').forEach(c => c.classList.toggle('active', c.dataset.filter === filter));
            renderHistoryCards(filter);
        });
    });
    if ($('#apiKey') && savedKey()) $('#apiKey').value = getMaskedKey(savedKey());
    renderVoiceGrid(); window.setTimeout(() => void initializeAuth(), 0);
} catch (err) { console.error("UI Error:", err); }