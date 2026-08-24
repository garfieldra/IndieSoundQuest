-- Hibernate maps @Lob String to LONGTEXT for MySQL.  Keep schema validation
-- strict while preserving the original V13 migration for already deployed DBs.
ALTER TABLE conversation MODIFY COLUMN summary LONGTEXT NULL;
ALTER TABLE conversation_message MODIFY COLUMN text_content LONGTEXT NULL;
