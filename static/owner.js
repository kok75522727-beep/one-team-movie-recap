async function loadDashboard() {
  try {
    const res = await fetch("/api/owner/metrics");
    if (!res.ok) throw new Error("Fetch failed");
    const data = await res.json();

    // ကိန်းဂဏန်းများ ထည့်သွင်းခြင်း
    document.getElementById("activeVipCount").textContent = data.active_vip ?? 0;
    document.getElementById("trialCount").textContent = data.simple_trial ?? 0;
    document.getElementById("pendingPaymentCount").textContent = data.pending_payments ?? 0;

    if (data.export_report) {
      document.getElementById("exportTotalCount").textContent = data.export_report.total ?? 0;
      document.getElementById("exportDoneCount").textContent = `Done: ${data.export_report.done ?? 0}`;
    }

    // VIP အသုံးပြုသူ စာရင်း
    const userContainer = document.getElementById("vipUserList");
    if (data.users && data.users.length > 0) {
      userContainer.innerHTML = data.users.map(u => `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:10px; background:#1e293b; border-radius:6px; margin-bottom:8px;">
          <div>
            <strong>${u.email}</strong>
            <div style="font-size:12px; color:#94a3b8;">Plan: ${u.tier.toUpperCase()} (${u.days_left} ရက်ကျန်)</div>
          </div>
          <span style="font-size:12px; color:#22c55e; font-weight:bold;">${u.is_admin ? "OWNER" : "ACTIVE"}</span>
        </div>
      `).join("");
    } else {
      userContainer.innerHTML = '<span style="color:#64748b;">အသုံးပြုသူ မရှိသေးပါ</span>';
    }

    // Payment Approval စာရင်း
    const payContainer = document.getElementById("paymentApprovalList");
    if (data.payments && data.payments.length > 0) {
      payContainer.innerHTML = data.payments.map(p => `
        <div style="background:#1e293b; padding:12px; border-radius:8px; margin-bottom:10px; border:1px solid #334155;">
          <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
            <strong>${p.payment_method} · ${p.plan_type}</strong>
            <span style="color:#ffd166; font-size:12px; font-weight:bold;">${p.status.toUpperCase()}</span>
          </div>
          <div style="font-size:12px; color:#94a3b8;">User: ${p.user_email}</div>
          <div style="font-size:12px; color:#94a3b8; margin-bottom:10px;">TxID: ${p.transaction_id} (${p.date})</div>
          
          ${p.status === "pending" ? `
            <div style="display:flex; gap:8px;">
              <button onclick="approvePay('${p.transaction_id}')" style="background:#22c55e; color:#000; border:none; padding:6px 14px; border-radius:4px; font-weight:bold; cursor:pointer;">အတည်ပြုမည်</button>
              <button onclick="rejectPay('${p.transaction_id}')" style="background:#ef4444; color:#fff; border:none; padding:6px 14px; border-radius:4px; font-weight:bold; cursor:pointer;">ပယ်ဖျက်မည်</button>
            </div>
          ` : `<span style="color:#22c55e; font-size:12px;">ပြီးစီးပါပြီ</span>`}
        </div>
      `).join("");
    } else {
      payContainer.innerHTML = '<span style="color:#64748b;">စစ်ဆေးရန် ငွေလွှဲ မရှိပါ</span>';
    }

  } catch (err) {
    document.getElementById("paymentApprovalList").innerHTML = '<span style="color:#ef4444;">ဒေတာ ဖတ်မရပါ</span>';
  }
}

// Payment အတည်ပြုခြင်း
window.approvePay = async function(txId) {
  if (!confirm("ဒီငွေလွှဲကို အတည်ပြုမှာ သေချာပါသလား?")) return;
  const res = await fetch("/api/owner/approve-payment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transaction_id: txId })
  });
  const data = await res.json();
  alert(data.message);
  loadDashboard();
};

// Payment ပယ်ဖျက်ခြင်း
window.rejectPay = async function(txId) {
  if (!confirm("ဒီငွေလွှဲကို ပယ်ဖျက်မှာ သေချာပါသလား?")) return;
  const res = await fetch("/api/owner/reject-payment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transaction_id: txId })
  });
  const data = await res.json();
  alert(data.message);
  loadDashboard();
};

// Bulk Repair
document.getElementById("bulkRepairBtn").addEventListener("click", async () => {
  const days = document.getElementById("repairDays").value;
  const res = await fetch("/api/owner/bulk-repair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ days: parseInt(days) })
  });
  const data = await res.json();
  alert(data.message);
  loadDashboard();
});

document.getElementById("refreshBtn").addEventListener("click", loadDashboard);
loadDashboard();