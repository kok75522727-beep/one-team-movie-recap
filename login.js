const $ = (selector) => document.querySelector(selector);
let auth;

function message(text, error = false) { const node = $('#loginMessage'); node.textContent = text; node.classList.toggle('error', error); }
async function bridgeSession(session) {
  if (!session?.access_token) return false;
  const response = await fetch('/api/browser-session', { method: 'POST', headers: { Authorization: `Bearer ${session.access_token}` } });
  return response.ok;
}
async function setup() {
  try {
    const config = await (await fetch('/api/auth-config')).json();
    if (config.enabled !== 'true' || !window.supabase) { message('Account Database setting မပြည့်သေးပါ။ Admin ကိုဆက်သွယ်ပါ', true); return; }
    auth = window.supabase.createClient(config.url, config.anon_key);
    const { data: { session } } = await auth.auth.getSession();
    if (session) { if (await bridgeSession(session)) window.location.replace('/'); return; }
    auth.auth.onAuthStateChange(async (_event, currentSession) => { if (currentSession && await bridgeSession(currentSession)) window.location.replace('/'); });
  } catch { message('Account စနစ်ကိုခဏမဆက်သွယ်နိုင်ပါ', true); }
}
async function emailLogin() {
  if (!auth) return; const email = $('#email').value.trim(); const password = $('#password').value;
  if (!email || !password) { message('Email နဲ့ Password ထည့်ပါ', true); return; }
  message('ဝင်နေပါတယ်…'); const { error } = await auth.auth.signInWithPassword({ email, password });
  if (error) message('Email သို့မဟုတ် Password မမှန်ပါ။ Email confirm ပြီးမပြီးစစ်ပါ', true);
}
async function signup() {
  if (!auth) return; const email = $('#email').value.trim(); const password = $('#password').value;
  if (!email || password.length < 8) { message('Email မှန်မှန်နဲ့ Password အနည်းဆုံး 8 လုံးထည့်ပါ', true); return; }
  message('Account ဖွင့်နေပါတယ်…'); const { error } = await auth.auth.signUp({ email, password, options: { emailRedirectTo: `${window.location.origin}/login` } });
  message(error ? 'Account ဖွင့်မရသေးပါ။ Email ကိုစစ်ပါ' : 'Email ထဲက Confirm link ကိုနှိပ်ပြီးမှ ဝင်ပါ', Boolean(error));
}
async function googleLogin() {
  if (!auth) return; message('Google သို့သွားနေပါတယ်…');
  const { error } = await auth.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: 'http://169.58.181.87:8080/login' } });
  if (error) message('Google Login setting မပြည့်သေးပါ။ Admin ကိုဆက်သွယ်ပါ', true);
}
$('#emailLogin').addEventListener('click', emailLogin); $('#signup').addEventListener('click', signup); $('#googleLogin').addEventListener('click', googleLogin); window.setTimeout(setup, 0);
