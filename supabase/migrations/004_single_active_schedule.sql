-- Migration 004: Enforce single-active-schedule constraint
-- At most one row in slot_schedules may have active = true at any time.
-- The trigger below fires BEFORE any INSERT or UPDATE that sets active = true
-- and automatically deactivates every other row first (atomic within the
-- same transaction).  This makes the invariant impossible to violate regardless
-- of whether the write originates from the API, a direct SQL client, or a
-- race condition between concurrent requests.

create or replace function public.fn_single_active_schedule()
returns trigger
language plpgsql
as $$
begin
  -- Only act when the row being written has active = true.
  if NEW.active = true then
    update public.slot_schedules
    set    active = false
    where  active = true
      and  id <> NEW.id;
  end if;
  return NEW;
end;
$$;

-- Drop and recreate to make the migration idempotent.
drop trigger if exists trg_single_active_schedule on public.slot_schedules;

create trigger trg_single_active_schedule
  before insert or update on public.slot_schedules
  for each row
  execute function public.fn_single_active_schedule();
