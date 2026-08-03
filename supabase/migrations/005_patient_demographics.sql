-- Migration 005: Patient demographics (name, gender, age, city)
alter table public.assessments
  add column if not exists name text,
  add column if not exists gender text,
  add column if not exists age integer,
  add column if not exists city text;

alter table public.bookings
  add column if not exists gender text,
  add column if not exists age integer,
  add column if not exists city text;

create index if not exists assessments_city_idx on public.assessments (city);
create index if not exists bookings_city_idx on public.bookings (city);
create index if not exists assessments_name_idx on public.assessments (name);
