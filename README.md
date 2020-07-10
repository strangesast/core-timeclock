# CORE Server
- User management / authenication

```
select a.employee_id,a.date_start,b.date_stop,a.date,(b.date_stop-a.date_start) as duration from timeclock_shifts as a join timeclock_shifts as b on a.employee_id=b.employee_id and a.date_stop=b.date_start;
```


```
select
	employee_id,
	date_start,
	date_stop,
	shift_num
from (
	select *,
		sum(case when nearby = true then 0 else 1 end) over (partition by employee_id order by num) as shift_num
	from (
		select *,
			row_number() over (partition by employee_id order by date_start asc) num,
			case when prev_stop is NULL then false else ((date_start - prev_stop) < interval '6 hour') end as nearby
		from (
			select *,
				lag(date_stop, 1) over (partition by employee_id order by date_start asc) prev_stop
			from timeclock_shifts
		) t
	) t
) t order by date_start desc;
```


# Create Machine Activity View
```
create view machine_execution_state_totals as select
	value,
	frac,
	cnt
from (
	select
		value,
		sum(interval) as frac,
		sum(1) as cnt
	from (
		select
			timestamp,
			value,
			(next_timestamp - timestamp)::interval
		from (
			select *,
			  lead(timestamp, 1) over (order by timestamp asc) as next_timestamp
			from (
				select
					timestamp::timestamp as timestamp,
					machine_id,
					value
				from (
					select *, row_number() over (partition by timestamp, machine_id, value order by timestamp asc) as n
					from machine_execution_state
					order by timestamp asc
				) t
				where t.n = 1
			) t
		) t
	) as t group by value
) t
```


# Machine Activity Fractions
```
select
	value,
	extract (epoch from frac) / (extract (epoch from (select sum(frac) from machine_execution_state_totals))),
	cnt
from machine_execution_state_totals;
```

# Machine Activity Fractions with inverse
```
select
	value,
	p,
	(100 - p),
	cnt
from (
	select
		value,
		extract (epoch from frac) / (extract (epoch from (select sum(frac) from machine_execution_state_totals))) * 100 as p,
		cnt
	from machine_execution_state_totals
) t
```

# Average Cycle Time
```
select avg(diff), count(diff) from (
	select
		*,
		ts1 - ts as diff
	from (
		select *, lead(ts, 1) over (order by timestamp) as ts1
		from (
			select *, timestamp::timestamp as ts from machine_execution_state
		) t
		order by ts desc
	) t
) t
where value = 'ACTIVE' and diff > interval '1 minute'
```
