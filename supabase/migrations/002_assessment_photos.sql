-- Assessment smile photo paths (compressed previews in Storage)
alter table public.assessments
  add column if not exists photo_front_path text,
  add column if not exists photo_left_path text,
  add column if not exists photo_right_path text;

-- Private bucket for compressed assessment photos (service role uploads/reads)
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'assessment-photos',
  'assessment-photos',
  false,
  2097152,
  array['image/jpeg']::text[]
)
on conflict (id) do update
set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
