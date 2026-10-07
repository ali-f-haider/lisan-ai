-- Read-only evidence supplied by Ali on 2026-10-07. NOT A MIGRATION.
CREATE OR REPLACE FUNCTION public.add_credits(uid uuid, amount integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER AS $function$
declare new_credits integer;
begin
  update public.profiles set credits = credits + amount where id = uid returning credits into new_credits;
  return coalesce(new_credits, 0);
end; $function$;

CREATE OR REPLACE FUNCTION public.deduct_credits(uid uuid, amount integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER AS $function$
declare new_credits integer;
begin
  update public.profiles set credits = greatest(credits - amount, 0) where id = uid returning credits into new_credits;
  return coalesce(new_credits, 0);
end; $function$;

CREATE OR REPLACE FUNCTION public.deduct_subscription_credits(uid uuid, amount integer)
RETURNS TABLE(taken integer, shortfall integer) LANGUAGE plpgsql AS $function$
DECLARE
  cur integer;
  take_amt integer;
BEGIN
  SELECT p.subscription_credits INTO cur FROM profiles p WHERE p.id = uid FOR UPDATE;
  IF cur IS NULL THEN
    cur := 0;
  END IF;
  take_amt := LEAST(GREATEST(cur, 0), amount);
  UPDATE profiles SET subscription_credits = cur - take_amt WHERE id = uid;
  RETURN QUERY SELECT take_amt, (amount - take_amt);
END;
$function$;
