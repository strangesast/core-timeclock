package main

import (
	"context"
	"database/sql"
	"github.com/davecgh/go-spew/spew"
	_ "github.com/go-sql-driver/mysql"
	"github.com/kolo/xmlrpc"
	_ "github.com/lib/pq"
	"log"
	"os"
	"os/signal"
	"reflect"
	"strings"
	"time"
)

type employeeRecord struct {
	ID         int
	Code       string
	FirstName  string
	MiddleName string
	LastName   string
	HireDate   string
}

type rpcPunch struct {
	ID           int       `xmlrpc:"Id"`
	OriginalDate time.Time `xmlrpc:"OriginalDate"`
}

type rpcTimecardLine struct {
	Date       time.Time `xmlrpc:"Date"`
	IsManual   bool      `xmlrpc:"IsManual"`
	StartPunch rpcPunch  `xmlrpc:"StartPunch"`
	StopPunch  rpcPunch  `xmlrpc:"StopPunch"`
}

type rpcEmployeeTimecards struct {
	EmployeeID int               `xmlrpc:"EmployeeId"`
	Timecards  []rpcTimecardLine `xmlrpc:"Timecards"`
}

var employeeColors = []string{"#1f78b4", "#33a02c", "#e31a1c", "#ff7f00", "#6a3d9a"}

func ping(ctx context.Context, pool *sql.DB) {
	ctx, cancel := context.WithTimeout(ctx, 1*time.Second)
	defer cancel()

	if err := pool.PingContext(ctx); err != nil {
		log.Fatalf("unable to connect to database: %v", err)
	}
}

func syncEmployees(ctx context.Context, mysql *sql.DB, postgres *sql.DB) error {
	employeesA := make(map[int]employeeRecord)
	employeesB := make(map[int]employeeRecord)

	res, err := postgres.QueryContext(ctx, "select id,code,first_name,middle_name,last_name,hire_date from employees")
	if err != nil {
		return err
	}

	for res.Next() {
		var record employeeRecord
		var hireDate time.Time
		res.Scan(&record.ID, &record.Code, &record.FirstName, &record.MiddleName, &record.LastName, &hireDate)
		record.HireDate = hireDate.Format("2006-01-02")
		employeesA[record.ID] = record
	}
	err = res.Err()

	res, err = mysql.QueryContext(ctx, "select id,Code,Name,MiddleName,LastName,HireDate from tam.inf_employee")
	if err != nil {
		return err
	}

	for res.Next() {
		var record employeeRecord
		var hireDate time.Time
		res.Scan(&record.ID, &record.Code, &record.FirstName, &record.MiddleName, &record.LastName, &hireDate)
		record.HireDate = hireDate.Format("2006-01-02")
		employeesB[record.ID] = record
	}
	err = res.Err()

	var (
		newEmployees      []int
		modifiedEmployees []int
	)

	for id, recordA := range employeesB {
		if recordB, ok := employeesA[id]; ok {
			if eql := reflect.DeepEqual(recordA, recordB); !eql {
				modifiedEmployees = append(modifiedEmployees, id)
			}
		} else {
			newEmployees = append(newEmployees, id)
		}
	}

	now := time.Now()

	tx, err := postgres.BeginTx(ctx, nil)

	if err != nil {
		panic(err)
	}

	for _, id := range modifiedEmployees {
		record := employeesB[id]
		_, err := tx.ExecContext(ctx, `
    update employees
    set code = $2, first_name = $3, middle_name = $4, last_name = $5, hire_date = $6, last_modified = $7
    where id = $1
    `, record.ID, record.Code, record.FirstName, record.MiddleName, record.LastName, record.HireDate, now)
		if err != nil {
			tx.Rollback()
			panic(err)
		}
	}

	for _, id := range newEmployees {
		record := employeesB[id]
		color := employeeColors[record.ID%len(employeeColors)]
		username := strings.Join([]string{record.FirstName[0:1], record.LastName[1:]}, "")

		var userID int

		err = tx.QueryRow(`
    insert into users(employee_id,username,color,password)
    values($1,$2,$3,crypt($4, gen_salt(\'bf\')))
    returning id
    `, record.ID, username, color, record.Code).Scan(&userID)
		if err != nil {
			tx.Rollback()
			panic(err)
		}

		_, err := tx.ExecContext(ctx, `
    insert into user_roles(user_id, role_id)
    values ($1, $2)
    `, userID, "isPaidHourly")
		if err != nil {
			tx.Rollback()
			panic(err)
		}

		_, err = tx.ExecContext(ctx, `
    insert into employees(id,code,first_name,middle_name,last_name,hire_date,color,last_modified,user_id)
    values ($1, $2, $3, $4, $5, $6, $7, $8)
    `, record.ID, record.Code, record.FirstName, record.MiddleName, record.LastName, record.HireDate, color, now, userID)

		if err != nil {
			tx.Rollback()
			panic(err)
		}
	}
	err = tx.Commit()

	if err != nil {
		panic(err)
	}

	return nil
}

func xmlrpcTest() {
	client, err := xmlrpc.NewClient("http://admin:Force!2049@localhost:3003/API/Timecard.ashx", nil)

	if err != nil {
		log.Fatalf("failed to create xmlrpc client: %v\n", err)
	}

	result := []rpcEmployeeTimecards{}

	toDate := time.Now()
	fromDate := toDate.AddDate(0, -2, 0)

	args := []interface{}{
		[]int{80},
		fromDate,
		toDate,
		false,
	}

	err = client.Call("GetTimecards", args, &result)

	if err != nil {
		log.Fatalf("failed to make rpc call: %v\n", err)
	}

	spew.Dump(result)
	// log.Printf("result: %+v\n", result)
}

func main() {
	ctx, stop := context.WithCancel(context.Background())
	defer stop()

	appSignal := make(chan os.Signal, 3)
	signal.Notify(appSignal, os.Interrupt)

	go func() {
		select {
		case <-appSignal:
			stop()
		}
	}()

	postgres, err := sql.Open("postgres", "postgres://postgres:password@localhost:5432/development?sslmode=disable")
	if err != nil {
		log.Fatalf("failed to setup db connection: %v\n", err)
	}
	defer postgres.Close()
	ping(ctx, postgres)

	mysql, err := sql.Open("mysql", "root:password@tcp(localhost:3306)/tam?parseTime=true")
	if err != nil {
		log.Fatalf("failed to setup db connection: %v\n", err.Error())
	}
	defer mysql.Close()
	ping(ctx, mysql)

	syncEmployees(ctx, mysql, postgres)
	// xmlrpcTest()
}
