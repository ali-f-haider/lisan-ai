-- Read-only evidence supplied by Ali on 2026-10-07, not an installation file.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $function$
begin
  insert into public.profiles (id, display_name, credits)
  values (new.id, coalesce(new.raw_user_meta_data->>'display_name', new.email), 100);
  return new;
end;
$function$;
CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users
FOR EACH ROW EXECUTE FUNCTION handle_new_user();
