-- Migration 006: Timestamped lead pipeline events
create table if not exists public.lead_events (
  id uuid primary key default gen_random_uuid(),
  assessment_id uuid references public.assessments (id) on delete cascade,
  email text,
  phone text,
  event_type text not null,
  title text not null,
  description text,
  actor text not null default 'Patient',
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists lead_events_assessment_id_idx on public.lead_events (assessment_id);
create index if not exists lead_events_email_idx on public.lead_events (email);
create index if not exists lead_events_created_at_idx on public.lead_events (created_at desc);

alter table public.lead_events enable row level security;
