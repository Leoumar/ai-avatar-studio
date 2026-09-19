-- =====================================================================
-- AI Avatar Studio - Supabase schema
--
-- Run this whole file once in:  Supabase Dashboard -> SQL Editor -> New query
-- It is safe to run again: every statement uses IF NOT EXISTS or DROP/CREATE.
-- =====================================================================

-- UUID helpers (gen_random_uuid lives in pgcrypto).
create extension if not exists "pgcrypto";


-- =====================================================================
-- 1. profiles
--    One row per account. id matches the Supabase Auth user id.
-- =====================================================================
create table if not exists public.profiles (
    id          uuid primary key references auth.users (id) on delete cascade,
    full_name   text,
    email       text unique,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

comment on table public.profiles is 'Public profile information for each authenticated user.';


-- =====================================================================
-- 2. avatar_generations
--    One row per generation job.
-- =====================================================================
create table if not exists public.avatar_generations (
    id                      uuid primary key default gen_random_uuid(),
    user_id                 uuid references public.profiles (id) on delete cascade,

    -- Paths inside the "avatar-files" storage bucket (never the file itself).
    image_path              text not null,
    voice_path              text not null,

    script_text             text not null,
    duration                integer not null check (duration > 0),
    quality                 text not null default '720p',

    status                  text not null default 'pending',
    progress                integer not null default 0 check (progress between 0 and 100),
    current_stage           text,

    -- RunPod job ids, useful for debugging a failed generation.
    cosyvoice_job_id        text,
    soulxflash_job_id       text,

    generated_audio_path    text,
    generated_video_path    text,

    error_message           text,

    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now(),

    constraint avatar_generations_status_check check (
        status in (
            'pending',
            'uploading',
            'generating_voice',
            'generating_avatar',
            'processing',
            'completed',
            'failed'
        )
    )
);

comment on table public.avatar_generations is 'One AI avatar video generation job.';


-- =====================================================================
-- 3. Indexes
-- =====================================================================
create index if not exists avatar_generations_user_id_idx
    on public.avatar_generations (user_id);

create index if not exists avatar_generations_created_at_idx
    on public.avatar_generations (created_at desc);

create index if not exists avatar_generations_status_idx
    on public.avatar_generations (status);

-- The history page always filters by user and sorts by date.
create index if not exists avatar_generations_user_created_idx
    on public.avatar_generations (user_id, created_at desc);


-- =====================================================================
-- 4. Keep updated_at accurate
-- =====================================================================
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
    before update on public.profiles
    for each row execute function public.set_updated_at();

drop trigger if exists avatar_generations_set_updated_at on public.avatar_generations;
create trigger avatar_generations_set_updated_at
    before update on public.avatar_generations
    for each row execute function public.set_updated_at();


-- =====================================================================
-- 5. Create a profile automatically when someone signs up
-- =====================================================================
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, email, full_name)
    values (
        new.id,
        new.email,
        coalesce(new.raw_user_meta_data ->> 'full_name', '')
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();


-- =====================================================================
-- 6. Row Level Security
--    A signed-in user can only see and change their own rows.
--    The backend uses the service-role key, which bypasses RLS on purpose -
--    that key must stay on the server.
-- =====================================================================
alter table public.profiles enable row level security;
alter table public.avatar_generations enable row level security;

-- profiles
drop policy if exists "Users can read own profile" on public.profiles;
create policy "Users can read own profile"
    on public.profiles for select
    using (auth.uid() = id);

drop policy if exists "Users can update own profile" on public.profiles;
create policy "Users can update own profile"
    on public.profiles for update
    using (auth.uid() = id)
    with check (auth.uid() = id);

drop policy if exists "Users can insert own profile" on public.profiles;
create policy "Users can insert own profile"
    on public.profiles for insert
    with check (auth.uid() = id);

-- avatar_generations
drop policy if exists "Users can read own generations" on public.avatar_generations;
create policy "Users can read own generations"
    on public.avatar_generations for select
    using (auth.uid() = user_id);

drop policy if exists "Users can insert own generations" on public.avatar_generations;
create policy "Users can insert own generations"
    on public.avatar_generations for insert
    with check (auth.uid() = user_id);

drop policy if exists "Users can update own generations" on public.avatar_generations;
create policy "Users can update own generations"
    on public.avatar_generations for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "Users can delete own generations" on public.avatar_generations;
create policy "Users can delete own generations"
    on public.avatar_generations for delete
    using (auth.uid() = user_id);


-- =====================================================================
-- 7. Storage bucket
--    Private bucket. Files are served through the FastAPI backend, so no
--    public read policy is needed.
-- =====================================================================
insert into storage.buckets (id, name, public)
values ('avatar-files', 'avatar-files', false)
on conflict (id) do nothing;

-- Folder convention inside the bucket:
--     images/{user_id}/{generation_id}.png
--     voices/{user_id}/{generation_id}.wav
--     generated-audio/{user_id}/{generation_id}.wav
--     generated-videos/{user_id}/{generation_id}.mp4
--
-- The policies below let a signed-in user reach only the folder named after
-- their own user id, which is the second path segment.

drop policy if exists "Users can read own files" on storage.objects;
create policy "Users can read own files"
    on storage.objects for select
    using (
        bucket_id = 'avatar-files'
        and (storage.foldername(name))[2] = auth.uid()::text
    );

drop policy if exists "Users can upload own files" on storage.objects;
create policy "Users can upload own files"
    on storage.objects for insert
    with check (
        bucket_id = 'avatar-files'
        and (storage.foldername(name))[2] = auth.uid()::text
    );

drop policy if exists "Users can delete own files" on storage.objects;
create policy "Users can delete own files"
    on storage.objects for delete
    using (
        bucket_id = 'avatar-files'
        and (storage.foldername(name))[2] = auth.uid()::text
    );


-- =====================================================================
-- 8. Development user
--    When AUTH_ENABLED=false the backend saves every generation under
--    DEV_USER_ID. profiles.id references auth.users, so the simplest way to
--    keep that reference valid is to leave user_id null in development, or to
--    create a real user in Authentication -> Users and paste its id into
--    DEV_USER_ID in your .env file.
--
--    If you prefer to let development rows exist without an auth user,
--    uncomment the next line to drop the foreign key:
--
-- alter table public.avatar_generations drop constraint avatar_generations_user_id_fkey;
-- =====================================================================
