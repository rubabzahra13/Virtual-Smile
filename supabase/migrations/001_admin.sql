-- Virtual Smile: assessments, bookings, seasonal slot schedules
-- Apply in Supabase SQL editor or: supabase db push

create extension if not exists "pgcrypto";

create table if not exists public.assessments (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  phone text not null,
  overall_score integer,
  category_scores jsonb,
  findings jsonb,
  report_text text,
  concerns text[] not null default '{}',
  treatments text[] not null default '{}',
  email_sent_at timestamptz,
  created_at timestamptz not null default now(),
  constraint assessments_email_lower check (email = lower(email))
);

create unique index if not exists assessments_email_uidx on public.assessments (email);
create unique index if not exists assessments_phone_uidx on public.assessments (phone);
create index if not exists assessments_created_at_idx on public.assessments (created_at desc);

create table if not exists public.bookings (
  id uuid primary key default gen_random_uuid(),
  assessment_id uuid references public.assessments (id) on delete set null,
  name text not null,
  email text not null,
  phone text not null,
  date date not null,
  time time not null,
  note text,
  source text not null default 'patient' check (source in ('patient', 'admin')),
  status text not null default 'confirmed' check (status in ('confirmed', 'cancelled')),
  created_at timestamptz not null default now()
);

create unique index if not exists bookings_slot_uidx
  on public.bookings (date, time)
  where status = 'confirmed';

create index if not exists bookings_date_idx on public.bookings (date);
create index if not exists bookings_created_at_idx on public.bookings (created_at desc);

create table if not exists public.slot_schedules (
  id uuid primary key default gen_random_uuid(),
  label text not null,
  start_date date not null,
  end_date date not null,
  days_of_week integer[] not null default '{1,2,3,4,5,6}',
  open_time time not null default '09:00',
  close_time time not null default '20:00',
  slot_minutes integer not null default 30 check (slot_minutes > 0),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  constraint slot_schedules_date_range check (end_date >= start_date)
);

create index if not exists slot_schedules_active_dates_idx
  on public.slot_schedules (active, start_date, end_date);

-- Seed default year-round hours (Mon–Sat 09:00–20:00)
insert into public.slot_schedules (
  label, start_date, end_date, days_of_week, open_time, close_time, slot_minutes, active
)
select
  'Standard hours',
  date '2020-01-01',
  date '2099-12-31',
  array[1, 2, 3, 4, 5, 6],
  time '09:00',
  time '20:00',
  30,
  true
where not exists (select 1 from public.slot_schedules limit 1);

alter table public.assessments enable row level security;
alter table public.bookings enable row level security;
alter table public.slot_schedules enable row level security;

-- No anon/authenticated policies: FastAPI uses the service role key only.
