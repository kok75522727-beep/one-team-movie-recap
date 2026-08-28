-- Run once in Supabase Dashboard -> SQL Editor after the legacy members schema.
-- This preserves the existing manual KBZPay/WavePay receipt and owner-approval workflow.

alter table public.members
    add column if not exists subscription_tier text not null default '',
    add column if not exists credit_balance integer not null default 0 check (credit_balance >= 0),
    add column if not exists credit_expires_at timestamptz,
    add column if not exists daily_quota_bonus integer not null default 0 check (daily_quota_bonus >= 0),
    add column if not exists quota_bonus_expires_at timestamptz;

alter table public.export_usage
    add column if not exists subscription_tier text not null default '';
create index if not exists export_usage_plan_outcome_day on public.export_usage (plan, outcome, export_day);

create table if not exists public.payment_requests (
    id bigint generated always as identity primary key,
    google_subject text not null references public.members(google_subject) on delete cascade,
    plan text not null default 'pro',
    request_kind text not null default 'plan',
    requested_tier text not null default '',
    requested_credits integer not null default 0,
    amount_mmk integer not null default 0,
    payment_method text not null,
    transaction_id text not null,
    receipt_key text not null default '',
    status text not null default 'submitted',
    admin_note text not null default '',
    submitted_at timestamptz not null default now(),
    reviewed_at timestamptz,
    reviewed_by text not null default ''
);

alter table public.payment_requests
    add column if not exists request_kind text not null default 'plan',
    add column if not exists requested_tier text not null default '',
    add column if not exists requested_credits integer not null default 0,
    add column if not exists amount_mmk integer not null default 0;

alter table public.payment_requests drop constraint if exists payment_requests_plan_check;
alter table public.payment_requests add constraint payment_requests_plan_check check (plan in ('pro', 'credits'));
alter table public.payment_requests drop constraint if exists payment_requests_request_kind_check;
alter table public.payment_requests add constraint payment_requests_request_kind_check check (request_kind in ('plan', 'credits'));
alter table public.payment_requests drop constraint if exists payment_requests_payment_method_check;
alter table public.payment_requests add constraint payment_requests_payment_method_check check (payment_method in ('KBZPay', 'WavePay'));
alter table public.payment_requests drop constraint if exists payment_requests_transaction_id_check;
alter table public.payment_requests add constraint payment_requests_transaction_id_check check (char_length(transaction_id) between 4 and 100);
alter table public.payment_requests drop constraint if exists payment_requests_requested_credits_check;
alter table public.payment_requests add constraint payment_requests_requested_credits_check check (requested_credits >= 0);
alter table public.payment_requests drop constraint if exists payment_requests_amount_mmk_check;
alter table public.payment_requests add constraint payment_requests_amount_mmk_check check (amount_mmk >= 0);
alter table public.payment_requests drop constraint if exists payment_requests_status_check;
alter table public.payment_requests add constraint payment_requests_status_check check (status in ('submitted', 'approved', 'rejected'));

create index if not exists payment_requests_pending_review on public.payment_requests (status, submitted_at);
alter table public.payment_requests enable row level security;

insert into storage.buckets (id, name, public)
values ('payment-receipts', 'payment-receipts', false)
on conflict (id) do nothing;

create table if not exists public.credit_ledger (
    id bigint generated always as identity primary key,
    google_subject text not null references public.members(google_subject) on delete cascade,
    credits_delta integer not null check (credits_delta <> 0),
    balance_after integer not null check (balance_after >= 0),
    action text not null check (action in ('purchase', 'consumed', 'refund', 'admin_adjustment')),
    reference_payment_id bigint references public.payment_requests(id) on delete set null,
    note text not null default '',
    created_at timestamptz not null default now()
);
create index if not exists credit_ledger_subject_created on public.credit_ledger (google_subject, created_at desc);
alter table public.credit_ledger enable row level security;

create or replace function public.grant_member_credits(
    p_google_subject text, p_credits integer, p_credit_expires_at timestamptz,
    p_payment_id bigint, p_note text default ''
) returns boolean language plpgsql security definer set search_path = public as $$
declare v_balance integer;
begin
    if p_credits <= 0 then return false; end if;
    update public.members
       set credit_balance = case when credit_expires_at is not null and credit_expires_at <= now() then p_credits else credit_balance + p_credits end,
           credit_expires_at = case when credit_expires_at is not null and credit_expires_at <= now() then p_credit_expires_at else greatest(coalesce(credit_expires_at, now()), p_credit_expires_at) end
     where google_subject = p_google_subject
     returning credit_balance into v_balance;
    if v_balance is null then return false; end if;
    insert into public.credit_ledger (google_subject, credits_delta, balance_after, action, reference_payment_id, note)
    values (p_google_subject, p_credits, v_balance, 'purchase', p_payment_id, coalesce(p_note, ''));
    return true;
end;
$$;

create or replace function public.consume_member_credits(
    p_google_subject text, p_credits integer, p_note text default ''
) returns boolean language plpgsql security definer set search_path = public as $$
declare v_balance integer;
begin
    if p_credits <= 0 then return false; end if;
    update public.members set credit_balance = credit_balance - p_credits
     where google_subject = p_google_subject and credit_balance >= p_credits
       and (credit_expires_at is null or credit_expires_at > now())
     returning credit_balance into v_balance;
    if v_balance is null then return false; end if;
    insert into public.credit_ledger (google_subject, credits_delta, balance_after, action, note)
    values (p_google_subject, -p_credits, v_balance, 'consumed', coalesce(p_note, ''));
    return true;
end;
$$;

-- The browser never receives the Supabase service-role key. Do not create an
-- anonymous policy that permits arbitrary payment approvals or receipt reads.
