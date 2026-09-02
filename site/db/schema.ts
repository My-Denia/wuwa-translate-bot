import { sql } from 'drizzle-orm';
import { check, integer, sqliteTable } from 'drizzle-orm/sqlite-core';

// One aggregate row; no content, identifiers, visitor keys or event history.
export const sharedPool = sqliteTable('shared_pool', {
  id: integer('id').primaryKey(),
  secondKey: integer('second_key').notNull(),
  minuteKey: integer('minute_key').notNull(),
  dayKey: integer('day_key').notNull(),
  upstreamUsed: integer('upstream_used').notNull(),
  translationMinuteUsed: integer('translation_minute_used').notNull(),
  termsUsed: integer('terms_used').notNull(),
  translationUsed: integer('translation_used').notNull(),
  characterUsed: integer('character_used').notNull(),
  metaUsed: integer('meta_used').notNull(),
}, table => [check('singleton', sql`${table.id} = 1`), check('nonnegative', sql`${table.secondKey} >= 0 AND ${table.minuteKey} >= 0 AND ${table.dayKey} >= 0 AND ${table.upstreamUsed} >= 0 AND ${table.translationMinuteUsed} >= 0 AND ${table.termsUsed} >= 0 AND ${table.translationUsed} >= 0 AND ${table.characterUsed} >= 0 AND ${table.metaUsed} >= 0`)]);
