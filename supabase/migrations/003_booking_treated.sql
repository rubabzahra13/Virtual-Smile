-- Track whether a confirmed appointment visit was marked treated
alter table public.bookings
  add column if not exists treated boolean not null default false;

comment on column public.bookings.treated is 'Admin-marked: patient visit completed / treated';
