package main

import (
	"context"
	"database/sql"
	"fmt"
	_ "github.com/go-sql-driver/mysql"
	_ "github.com/lib/pq"
	"github.com/spf13/viper"
	"log"
	"testing"
	"time"
)

var mysqldb *sql.DB
var pgdb *sql.DB

func createEmployeeRecord() {
	// if user record exists update it, else create it
	// upsert employee record
}

var mockEmployees = [...]employeeRecord{
	employeeRecord{
		1,
		"1234",
		"Name First",
		"Name Middle",
		"Name Last",
		"2020-01-01",
	},
	employeeRecord{
		2,
		"2345",
		"Name First",
		"Name Middle",
		"Name Last",
		"2020-01-01",
	},
}

func resetTables() {

}

// TestSyncEmployees tests that employees are synced from mysql to postgres
func TestSyncEmployees(t *testing.T) {
	ctx := context.Background()
	const (
		MYSQLEmployeeTable = "tam.inf_employee"
		PGEmployeeTable    = "employees"
		PGUserTable        = "users"
	)
	mysqldb.ExecContext(ctx, fmt.Sprintf("truncate table %s", MYSQLEmployeeTable))
	pgdb.ExecContext(ctx, fmt.Sprintf("truncate %s", PGEmployeeTable))
	pgdb.ExecContext(ctx, fmt.Sprintf("truncate %s", PGUserTable))

	// create a few employees in mysql
	q := fmt.Sprintf(`insert into %s(id,Code,Name,MiddleName,LastName,HireDate)
values ($1, $2, $3, $4, $5, $6)`, MYSQLEmployeeTable)
	for _, record := range mockEmployees {
		_, err := mysqldb.ExecContext(ctx, q, record.ID, record.Code, record.FirstName, record.MiddleName, record.LastName, record.HireDate)
		if err != nil {
			log.Fatal(err)
		}
	}

	err := syncEmployees(ctx, mysqldb, pgdb)

	if err != nil {
		log.Fatalf("failed to sync employees: %v\n", err)
	}

	row := mysqldb.QueryRowContext(ctx, fmt.Sprintf(`select count(*) from %s`, PGEmployeeTable))
	if err = row.Err(); err != nil {
		log.Fatalf("failed to sync employess: %v\n", err)
	}
	var count int
	row.Scan(&count)

	if count != len(mockEmployees) {
		t.Errorf("expected to create %d new records in target db\n", count)
	}

	// run syncEmployees
	// check that postgres match employees
}

func TestCreateEmployee(t *testing.T) {}

func setup() {
	err := viper.ReadInConfig()
	if err != nil {
		log.Fatalf("Fatal error config file: %s \n", err)
	}

	//db, err := sql.Open("mysql", "user:password@/dbname")
	db, err := sql.Open("mysql", "/testing")
	if err != nil {
		panic(err)
	}

	db.SetConnMaxLifetime(time.Minute * 3)
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(10)
}
