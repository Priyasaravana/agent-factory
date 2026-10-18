// The tables, created on first use (`db()`): `create table if not exists …` statements, no
// migration tool. Add a table here when a requirement needs to store data.
export const SCHEMA = `
create table if not exists app_info (
  key text primary key,
  value text not null
);
`;
