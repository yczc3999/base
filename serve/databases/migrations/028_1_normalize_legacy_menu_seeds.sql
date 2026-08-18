-- 028.1: Normalize legacy SEO/settings seeds before 029 creates canonical menus.
--
-- Fresh Base installation executes seo.sql before numbered migrations. That legacy
-- seed uses ids 801/803/804/706 with slugs that 029 recreates at canonical ids.
-- Remove only those exact legacy records (and their permission descendants) so the
-- unique slug constraint cannot make a clean installation fail.

BEGIN;

DELETE FROM role_menus WHERE menu_id IN (
  WITH RECURSIVE legacy_menu AS (
    SELECT id FROM menus
    WHERE (id = 801 AND slug = 'seo-dashboard')
       OR (id = 803 AND slug = 'seo-log')
       OR (id = 804 AND slug = 'seo-sitemap')
       OR (id = 706 AND slug = 'settings-seo')
    UNION ALL
    SELECT child.id
    FROM menus child
    JOIN legacy_menu parent ON child.parent_id = parent.id
  )
  SELECT id FROM legacy_menu
);

DELETE FROM menus WHERE id IN (
  WITH RECURSIVE legacy_menu AS (
    SELECT id FROM menus
    WHERE (id = 801 AND slug = 'seo-dashboard')
       OR (id = 803 AND slug = 'seo-log')
       OR (id = 804 AND slug = 'seo-sitemap')
       OR (id = 706 AND slug = 'settings-seo')
    UNION ALL
    SELECT child.id
    FROM menus child
    JOIN legacy_menu parent ON child.parent_id = parent.id
  )
  SELECT id FROM legacy_menu
);

COMMIT;
