CREATE TABLE `shared_pool` (
	`id` integer PRIMARY KEY NOT NULL,
	`second_key` integer NOT NULL,
	`minute_key` integer NOT NULL,
	`day_key` integer NOT NULL,
	`upstream_used` integer NOT NULL,
	`translation_minute_used` integer NOT NULL,
	`terms_used` integer NOT NULL,
	`translation_used` integer NOT NULL,
	`character_used` integer NOT NULL,
	`meta_used` integer NOT NULL,
	CONSTRAINT "singleton" CHECK("shared_pool"."id" = 1),
	CONSTRAINT "nonnegative" CHECK("shared_pool"."second_key" >= 0 AND "shared_pool"."minute_key" >= 0 AND "shared_pool"."day_key" >= 0 AND "shared_pool"."upstream_used" >= 0 AND "shared_pool"."translation_minute_used" >= 0 AND "shared_pool"."terms_used" >= 0 AND "shared_pool"."translation_used" >= 0 AND "shared_pool"."character_used" >= 0 AND "shared_pool"."meta_used" >= 0)
);
